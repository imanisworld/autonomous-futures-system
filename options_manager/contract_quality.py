"""Phase 3 — contract quality / market data gate.

Pure, deterministic evaluation of a supplied ContractMarketSnapshot against a
Phase 1 OptionTradePacket, before that packet may move forward. No broker
calls, no order calls, no HTTP, no Discord, no file writes, no provider
fetching — this module performs no I/O of any kind. It only reads a packet, a
snapshot, and a config object, and returns a result.

This gate does NOT fetch contract/market data. It only validates snapshots
that are handed to it. Fetching real options data is a later phase.

Independent of risk/risk_engine.py (futures) and risk/options_risk_engine.py
(reference only, not imported) — this is options_manager's own gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional

from .config import OptionsManagerConfig
from .models import OptionTradePacket

MOCK_PROVIDER_NAMES = ("mock", "test")


@dataclass(kw_only=True)
class ContractMarketSnapshot:
    ticker: str
    contract_symbol: Optional[str]
    bid: Optional[float]
    ask: Optional[float]
    last: Optional[float]
    volume: Optional[int]
    open_interest: Optional[int]
    implied_volatility: Optional[float]
    delta: Optional[float]
    theta: Optional[float]
    underlying_price: Optional[float]
    quote_timestamp: Optional[datetime]
    provider: str
    is_snapshot_complete: bool


@dataclass
class ContractQualityResult:
    approved: bool
    status: Literal["APPROVED", "REJECTED", "DATA_BLOCKED"]
    failed_rule: Optional[str] = None
    reason: str = ""
    warnings: list[str] = field(default_factory=list)


def _approved(warnings: list[str]) -> ContractQualityResult:
    return ContractQualityResult(
        approved=True, status="APPROVED", failed_rule=None, reason="", warnings=warnings
    )


def _rejected(rule: str, reason: str) -> ContractQualityResult:
    return ContractQualityResult(
        approved=False, status="REJECTED", failed_rule=rule, reason=reason, warnings=[]
    )


def _data_blocked(rule: str, reason: str) -> ContractQualityResult:
    return ContractQualityResult(
        approved=False,
        status="DATA_BLOCKED",
        failed_rule=rule,
        reason=reason,
        warnings=[],
    )


def evaluate_contract_quality(
    packet: OptionTradePacket,
    snapshot: ContractMarketSnapshot,
    config: OptionsManagerConfig,
) -> ContractQualityResult:
    """Pure function of (packet, snapshot, config) -> ContractQualityResult.

    config is required and must be passed explicitly by the caller — this
    function itself must never read env vars, .env files, or any other
    external mutable state, or it stops being deterministic. It never fetches
    data from a provider; it only validates the snapshot it's given.
    """
    cfg = config
    warnings: list[str] = []

    # 0. Only PENDING packets may be quality-reviewed — defensive re-check,
    # same pattern as risk_gate.py's first check. A packet that failed Phase 1
    # validation (or was otherwise never PENDING) must not come back APPROVED
    # here on the strength of good market data alone.
    if packet.status != "PENDING":
        return _rejected(
            "packet_not_pending",
            f"packet status is '{packet.status}' (must be PENDING); "
            f"original rejection_reason={packet.rejection_reason!r}",
        )

    # 0b. packet.entry_price must be structurally valid — hard reject, not a
    # warn-and-skip. A quality gate must not approve on top of invalid packet
    # data; it also guarantees the underlying-price diff below never divides
    # by zero/negative.
    if packet.entry_price <= 0:
        return _rejected(
            "entry_price_invalid",
            f"packet.entry_price {packet.entry_price} is invalid (must be > 0)",
        )

    # 1. Provider.
    provider = (snapshot.provider or "").strip()
    if not provider:
        return _data_blocked(
            "provider_missing", "snapshot.provider is empty/missing"
        )
    if provider.lower() in MOCK_PROVIDER_NAMES and not cfg.quality_allow_mock_provider:
        return _rejected(
            "provider_not_allowed",
            f"provider '{provider}' is not allowed (quality_allow_mock_provider=False)",
        )

    # 2. Bid/ask presence.
    quote_present = snapshot.bid is not None and snapshot.ask is not None
    if not quote_present:
        if cfg.quality_missing_quote_blocks:
            return _data_blocked(
                "quote_missing", "snapshot bid/ask is missing"
            )
        warnings.append("snapshot bid/ask is missing; skipping quote-dependent checks")

    # 3. Volume/open interest presence.
    oi_volume_present = snapshot.volume is not None and snapshot.open_interest is not None
    if not oi_volume_present:
        if cfg.quality_missing_oi_volume_blocks:
            return _data_blocked(
                "volume_or_oi_missing", "snapshot volume/open_interest is missing"
            )
        warnings.append(
            "snapshot volume/open_interest is missing; skipping volume/OI checks"
        )

    # 4. Greeks presence (IV/delta/theta) — bucketed together per spec.
    greeks_missing = (
        snapshot.implied_volatility is None
        or snapshot.delta is None
        or snapshot.theta is None
    )
    if greeks_missing:
        if cfg.quality_missing_greeks_blocks:
            return _data_blocked(
                "greeks_missing", "snapshot IV/delta/theta is missing"
            )
        warnings.append("snapshot IV/delta/theta is missing; skipping Greeks checks")

    # 5. Bid/ask sanity (only if present).
    if quote_present:
        if snapshot.bid <= 0:
            return _rejected("bid_ask_invalid", f"bid {snapshot.bid} must be > 0")
        if snapshot.ask <= 0:
            return _rejected("bid_ask_invalid", f"ask {snapshot.ask} must be > 0")
        if snapshot.ask < snapshot.bid:
            return _rejected(
                "bid_ask_invalid", f"ask {snapshot.ask} must be >= bid {snapshot.bid}"
            )

        # 6. Spread %.
        midpoint = (snapshot.ask + snapshot.bid) / 2
        spread_pct = (snapshot.ask - snapshot.bid) / midpoint * 100
        if spread_pct > cfg.quality_max_spread_percent:
            return _rejected(
                "spread_too_wide",
                f"spread {spread_pct:.2f}% exceeds max {cfg.quality_max_spread_percent}%",
            )

        # 9. Premium alignment.
        if snapshot.ask > packet.max_premium:
            return _rejected(
                "premium_exceeds_packet_max",
                f"ask {snapshot.ask} exceeds packet max_premium {packet.max_premium}",
            )

    # 7. Volume minimum (only if present).
    if oi_volume_present:
        if snapshot.volume < cfg.quality_min_option_volume:
            return _rejected(
                "volume_too_low",
                f"volume {snapshot.volume} below minimum {cfg.quality_min_option_volume}",
            )

        # 8. Open interest minimum (only if present).
        if snapshot.open_interest < cfg.quality_min_open_interest:
            return _rejected(
                "open_interest_too_low",
                f"open_interest {snapshot.open_interest} below minimum "
                f"{cfg.quality_min_open_interest}",
            )

    # 10. Underlying price sanity.
    if snapshot.underlying_price is None:
        if cfg.quality_require_underlying_price:
            return _data_blocked(
                "underlying_price_missing", "snapshot.underlying_price is missing"
            )
        warnings.append(
            "snapshot.underlying_price is missing; skipping underlying price check"
        )
    else:
        # packet.entry_price > 0 is guaranteed by the 0b check above.
        diff_pct = (
            abs(snapshot.underlying_price - packet.entry_price)
            / packet.entry_price
            * 100
        )
        if diff_pct > cfg.quality_underlying_price_max_diff_percent:
            if cfg.quality_reject_underlying_price_mismatch:
                return _rejected(
                    "underlying_price_mismatch",
                    f"underlying_price {snapshot.underlying_price} differs from "
                    f"packet.entry_price {packet.entry_price} by {diff_pct:.2f}%",
                )
            warnings.append(
                f"underlying_price {snapshot.underlying_price} differs from "
                f"packet.entry_price {packet.entry_price} by {diff_pct:.2f}%"
            )

    # 11. Quote freshness.
    if snapshot.quote_timestamp is None:
        if cfg.quality_require_quote_timestamp:
            return _data_blocked(
                "quote_timestamp_missing", "snapshot.quote_timestamp is missing"
            )
        warnings.append(
            "snapshot.quote_timestamp is missing; skipping freshness check"
        )
    elif snapshot.quote_timestamp.tzinfo is None:
        # A naive timestamp has no reliable timezone — we cannot safely
        # compute its age. Treat as untrustworthy data rather than assume UTC
        # (or crash comparing naive/aware datetimes).
        return _data_blocked(
            "quote_timestamp_not_timezone_aware",
            "snapshot.quote_timestamp has no timezone info; cannot assess freshness",
        )
    else:
        age_seconds = (
            datetime.now(timezone.utc) - snapshot.quote_timestamp
        ).total_seconds()
        if age_seconds > cfg.quality_max_quote_age_seconds:
            return _data_blocked(
                "quote_stale",
                f"quote age {age_seconds:.0f}s exceeds max "
                f"{cfg.quality_max_quote_age_seconds}s",
            )

    # 12. Delta bounds (only if present; missing already handled in step 4).
    if snapshot.delta is not None:
        abs_delta = abs(snapshot.delta)
        if abs_delta < cfg.quality_min_abs_delta or abs_delta > cfg.quality_max_abs_delta:
            return _rejected(
                "delta_out_of_range",
                f"abs(delta) {abs_delta} outside allowed range "
                f"[{cfg.quality_min_abs_delta}, {cfg.quality_max_abs_delta}]",
            )
        if packet.direction == "CALL" and snapshot.delta < 0:
            warnings.append(
                f"CALL delta {snapshot.delta} is negative (provider sign convention?)"
            )

    # 13. Theta sanity (only if present and ask present; warn only, never reject).
    if snapshot.theta is not None and quote_present and snapshot.ask > 0:
        theta_ratio = abs(snapshot.theta) / snapshot.ask
        if theta_ratio > cfg.quality_theta_warning_ratio:
            warnings.append(
                f"theta {snapshot.theta} is {theta_ratio:.2f}x ask premium "
                f"(warning threshold {cfg.quality_theta_warning_ratio})"
            )

    # 14. IV sanity (only if present).
    if snapshot.implied_volatility is not None:
        if snapshot.implied_volatility <= 0:
            return _rejected(
                "iv_invalid",
                f"implied_volatility {snapshot.implied_volatility} must be > 0",
            )
        if snapshot.implied_volatility > cfg.quality_high_iv_warning_threshold:
            warnings.append(
                f"implied_volatility {snapshot.implied_volatility} exceeds warning "
                f"threshold {cfg.quality_high_iv_warning_threshold}"
            )

    return _approved(warnings)
