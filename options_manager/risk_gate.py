"""Phase 2 — options risk gate.

Pure, deterministic re-validation of a Phase 1 OptionTradePacket before it may
move forward. No broker calls, no order calls, no HTTP, no Discord, no file
writes — this module performs no I/O of any kind. It only reads a packet and
a config object and returns a result.

Independent of risk/risk_engine.py (futures) and risk/options_risk_engine.py
(reference only, not imported) — this is options_manager's own gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Optional

from .config import OptionsManagerConfig
from .models import OptionTradePacket

KNOWN_GEX_REGIMES = ("LOW_PINNING", "HIGH_PINNING", "NEG_GAMMA", "POS_GAMMA")


@dataclass
class RiskGateResult:
    approved: bool
    status: Literal["APPROVED", "REJECTED", "DATA_BLOCKED"]
    failed_rule: Optional[str] = None
    reason: str = ""
    warnings: list[str] = field(default_factory=list)


def _approved(warnings: list[str]) -> RiskGateResult:
    return RiskGateResult(
        approved=True, status="APPROVED", failed_rule=None, reason="", warnings=warnings
    )


def _rejected(rule: str, reason: str) -> RiskGateResult:
    return RiskGateResult(
        approved=False, status="REJECTED", failed_rule=rule, reason=reason, warnings=[]
    )


def _data_blocked(rule: str, reason: str) -> RiskGateResult:
    return RiskGateResult(
        approved=False,
        status="DATA_BLOCKED",
        failed_rule=rule,
        reason=reason,
        warnings=[],
    )


def evaluate_packet(
    packet: OptionTradePacket, config: OptionsManagerConfig
) -> RiskGateResult:
    """Pure function of (packet, config) -> RiskGateResult.

    config is required and must be passed explicitly by the caller (e.g. via
    OptionsManagerConfig.from_env() at the call site) — this function itself
    must never read env vars, .env files, or any other external mutable
    state, or it stops being deterministic.
    """
    cfg = config
    warnings: list[str] = []

    # 1. Only PENDING packets may be risk-reviewed.
    if packet.status != "PENDING":
        return _rejected(
            "packet_not_pending",
            f"packet status is '{packet.status}' (must be PENDING); "
            f"original rejection_reason={packet.rejection_reason!r}",
        )

    # 2. Premium cap.
    if packet.max_premium > cfg.risk_max_premium:
        return _rejected(
            "premium_cap",
            f"max_premium {packet.max_premium} exceeds risk cap {cfg.risk_max_premium}",
        )

    # 3. Contract count cap.
    if packet.max_contracts > cfg.risk_max_contracts:
        return _rejected(
            "contracts_cap",
            f"max_contracts {packet.max_contracts} exceeds risk cap {cfg.risk_max_contracts}",
        )

    # 4. Total planned premium risk.
    total_risk = packet.max_premium * 100 * packet.max_contracts
    if total_risk > cfg.risk_max_total_premium_dollars:
        return _rejected(
            "total_premium_risk",
            f"total premium risk ${total_risk:.2f} exceeds cap "
            f"${cfg.risk_max_total_premium_dollars:.2f}",
        )

    # 5. DTE requirement (independent re-check, own config, not packet_builder's).
    days_out = (packet.contract_expiry - date.today()).days
    if days_out < cfg.risk_min_dte_days:
        return _rejected(
            "min_dte",
            f"contract_expiry {days_out}d out below risk minimum {cfg.risk_min_dte_days}d",
        )

    # 6. Direction/target sanity — defensive re-check of packet_builder's own rule.
    if packet.direction == "CALL" and packet.price_target <= packet.entry_price:
        return _rejected(
            "target_direction_mismatch",
            "price_target must be above entry_price for CALL",
        )
    if packet.direction == "PUT" and packet.price_target >= packet.entry_price:
        return _rejected(
            "target_direction_mismatch",
            "price_target must be below entry_price for PUT",
        )

    # 7. Signa minimum.
    if packet.signa_score < cfg.risk_min_signa_score:
        return _rejected(
            "signa_score_min",
            f"signa_score {packet.signa_score} below risk minimum {cfg.risk_min_signa_score}",
        )
    if packet.signa_grade not in cfg.risk_allowed_grades:
        return _rejected(
            "signa_grade_not_allowed",
            f"signa_grade '{packet.signa_grade}' not in allowed grades {cfg.risk_allowed_grades}",
        )
    if packet.direction == "CALL" and packet.signa_bias != "BULLISH":
        return _rejected(
            "signa_bias_mismatch",
            f"signa_bias '{packet.signa_bias}' does not align with CALL (requires BULLISH)",
        )
    if packet.direction == "PUT" and packet.signa_bias != "BEARISH":
        return _rejected(
            "signa_bias_mismatch",
            f"signa_bias '{packet.signa_bias}' does not align with PUT (requires BEARISH)",
        )

    # 8. GEX regime handling. OPTIONAL by default — see risk_reject_empty_gex_regime.
    # An absent regime is a missing enrichment, not a blocking data gap: the packet
    # still carries Signa, direction, and contract-quality evidence. Rejecting here
    # would make a vendor GEX feed a hard dependency of the whole lane.
    regime = (packet.gex_regime or "").strip()
    if not regime:
        if cfg.risk_reject_empty_gex_regime:
            return _data_blocked(
                "gex_regime_missing",
                "gex_regime is empty/missing; insufficient data to assess",
            )
        warnings.append(
            "GEX_UNAVAILABLE: gex_regime is empty/missing; approved on Signa "
            "context only, no gamma-wall targeting"
        )
    elif regime not in KNOWN_GEX_REGIMES:
        # Covers both the literal "UNKNOWN" value and any unrecognized,
        # provider-specific label — treated identically, per design: GEX
        # labels vary by provider, so warn rather than assume the label is
        # invalid.
        if cfg.risk_warn_unknown_gex_regime:
            warnings.append(f"gex_regime '{regime}' is not a recognized regime")

    # 9. Account tag.
    if packet.account_tag not in cfg.risk_allowed_account_tags:
        return _rejected(
            "account_tag_not_allowed",
            f"account_tag '{packet.account_tag}' not in allowed tags "
            f"{cfg.risk_allowed_account_tags}",
        )

    return _approved(warnings)
