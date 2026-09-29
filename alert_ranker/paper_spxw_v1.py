"""SPX → SPXW paper policy (OPTIONS_PAPER_SPXW_V1).

Isolated from ``OPTIONS_PAPER_V1``. Allows 0DTE and 1+DTE SPXW contracts as
separate cohorts without changing equity V1's MIN_DTE=14 rule. Risk caps,
liquidity gates, premium stop, and late-entry discipline match V1 numbers.
Paper/advisory only — no broker or order capability.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from alert_ranker.paper_v1 import (
    DEFAULT_MIN_REMAINING_RR,
    ENTRY_LATE_STATUS,
    entry_geometry_state,
    entry_late_reason,
    remaining_reward_to_risk,
)

POLICY_ID = "OPTIONS_PAPER_SPXW_V1"
SIGNAL_UNDERLYING = "SPX"
CONTRACT_ROOT = "SPXW"
SETTLEMENT = "CASH_SETTLED_INDEX"

MAX_TRADE_RISK_DOLLARS = 300.0
MAX_AGGREGATE_OPEN_RISK_DOLLARS = 1_000.0
MAX_SANITY_DTE = 730
PREMIUM_STOP_ADVERSE_PERCENT = 25.0
PREMIUM_STOP_MULTIPLIER = 0.75
CONTRACT_MULTIPLIER = 100
MAX_SPREAD_PERCENT = 10.0
MIN_OPTION_VOLUME = 100
MIN_OPEN_INTEREST = 500
MIN_ABS_DELTA = 0.30
MAX_ABS_DELTA = 0.70
DELTA_TARGET = 0.40

COHORT_0DTE = "0DTE"
COHORT_1_PLUS = "1_PLUS_DTE"
ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class ExpiryChoice:
    expiration: str
    dte: int
    cohort: str
    bucket: str
    warning: str = ""


@dataclass(frozen=True)
class ContractChoice:
    symbol: str
    option_type: str
    strike: float
    bid: float
    ask: float
    mid: float
    volume: float
    open_interest: float
    spread_percent: float
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    implied_volatility: float | None = None
    quote_timestamp: str | None = None
    quote_source: str | None = None
    warning: str = ""


@dataclass(frozen=True)
class PolicyDecision:
    status: str
    reason: str = ""
    expiry: ExpiryChoice | None = None
    contract: ContractChoice | None = None

    @property
    def valid(self) -> bool:
        return self.status == "VALID"


def _as_date(value: Any) -> date | None:
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _session_date(now: datetime | date) -> date:
    if isinstance(now, datetime):
        if now.tzinfo is None:
            return now.date()
        return now.astimezone(ET).date()
    return now


def dte_for(expiration: Any, now: datetime | date) -> int | None:
    expiry = _as_date(expiration)
    if expiry is None:
        return None
    return (expiry - _session_date(now)).days


def classify_dte_cohort(dte: int | None) -> str | None:
    if dte is None or dte < 0 or dte > MAX_SANITY_DTE:
        return None
    if dte == 0:
        return COHORT_0DTE
    return COHORT_1_PLUS


def _num(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def choose_expiration(expirations: Iterable[Any], now: datetime) -> PolicyDecision:
    """Pick an SPXW expiration that may be 0DTE or 1+DTE.

    Nearest sane expiration first (fair look at 0DTE without claiming it is
    better). Cohort is tagged from DTE, never from equity V1 bands.
    """
    parsed: list[tuple[str, int, str]] = []
    for raw in expirations:
        expiry = _as_date(raw)
        if expiry is None:
            continue
        dte = (expiry - _session_date(now)).days
        cohort = classify_dte_cohort(dte)
        if cohort is None:
            continue
        parsed.append((expiry.isoformat(), dte, cohort))

    if not parsed:
        return PolicyDecision("DATA_INVALID", "no_expiration_in_spxw_dte_range")

    expiration, dte, cohort = min(parsed, key=lambda row: (row[1], row[0]))
    bucket = "DTE_0" if cohort == COHORT_0DTE else "DTE_1_PLUS"
    return PolicyDecision(
        "VALID",
        expiry=ExpiryChoice(
            expiration=expiration,
            dte=dte,
            cohort=cohort,
            bucket=bucket,
        ),
    )


def list_eligible_expirations(expirations: Iterable[Any], now: datetime) -> list[ExpiryChoice]:
    """All sane SPXW expirations with cohort tags (for multi-expiry selection)."""
    out: list[ExpiryChoice] = []
    for raw in expirations:
        expiry = _as_date(raw)
        if expiry is None:
            continue
        dte = (expiry - _session_date(now)).days
        cohort = classify_dte_cohort(dte)
        if cohort is None:
            continue
        bucket = "DTE_0" if cohort == COHORT_0DTE else "DTE_1_PLUS"
        out.append(
            ExpiryChoice(
                expiration=expiry.isoformat(),
                dte=dte,
                cohort=cohort,
                bucket=bucket,
            )
        )
    return sorted(out, key=lambda item: (item.dte, item.expiration))


def _quality(contract: Any, *, max_quote_age_seconds: float | None = None, now: datetime | None = None) -> tuple[ContractChoice | None, str]:
    symbol = str(getattr(contract, "symbol", "") or "").strip()
    option_type = str(getattr(contract, "option_type", "") or "").upper()
    strike = _num(getattr(contract, "strike", None))
    bid = _num(getattr(contract, "bid", None))
    ask = _num(getattr(contract, "ask", None))
    mid = _num(getattr(contract, "mid", None))
    volume = _num(getattr(contract, "volume", None))
    oi = _num(getattr(contract, "open_interest", None))
    delta = _num(getattr(contract, "delta", None))
    gamma = _num(getattr(contract, "gamma", None))
    theta = _num(getattr(contract, "theta", None))
    iv = _num(getattr(contract, "implied_volatility", None))
    quote_timestamp = getattr(contract, "quote_timestamp", None)
    quote_source = getattr(contract, "source", None)

    if getattr(contract, "stale", False):
        return None, "stale_quote"
    if not symbol or option_type not in {"CALL", "PUT"} or strike is None or strike <= 0:
        return None, "missing_contract_identity"
    if bid is None or ask is None or bid <= 0 or ask <= bid:
        return None, "missing_or_invalid_bid_ask"
    if mid is None or mid <= 0:
        mid = (bid + ask) / 2.0
    spread_percent = ((ask - bid) / mid) * 100.0
    if spread_percent > MAX_SPREAD_PERCENT:
        return None, "spread_too_wide"
    if volume is None or volume < MIN_OPTION_VOLUME:
        return None, "volume_too_low_or_missing"
    if oi is None or oi < MIN_OPEN_INTEREST:
        return None, "open_interest_too_low_or_missing"
    if delta is not None and not (MIN_ABS_DELTA <= abs(delta) <= MAX_ABS_DELTA):
        return None, "delta_outside_quality_band"
    if max_quote_age_seconds is not None and max_quote_age_seconds > 0:
        if quote_timestamp in (None, ""):
            return None, "missing_quote_timestamp"
        if now is not None:
            try:
                ts = datetime.fromisoformat(str(quote_timestamp).replace("Z", "+00:00"))
            except ValueError:
                return None, "invalid_quote_timestamp"
            ref = now if now.tzinfo is not None else now.replace(tzinfo=ET)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=ET)
            age = (ref.astimezone(ts.tzinfo) - ts).total_seconds()
            if age > max_quote_age_seconds:
                return None, "stale_quote"

    warning = "GREEKS_PARTIAL" if delta is None else ""
    return (
        ContractChoice(
            symbol=symbol,
            option_type=option_type,
            strike=strike,
            bid=bid,
            ask=ask,
            mid=mid,
            volume=volume,
            open_interest=oi,
            spread_percent=spread_percent,
            delta=delta,
            gamma=gamma,
            theta=theta,
            implied_volatility=iv,
            quote_timestamp=str(quote_timestamp) if quote_timestamp else None,
            quote_source=str(quote_source) if quote_source else None,
            warning=warning,
        ),
        "",
    )


def choose_contract(
    contracts: Iterable[Any],
    *,
    option_type: str,
    underlying_price: float | None,
    max_quote_age_seconds: float | None = None,
    now: datetime | None = None,
) -> PolicyDecision:
    side = option_type.upper()
    if side not in {"CALL", "PUT"}:
        return PolicyDecision("DATA_INVALID", "invalid_option_type")

    valid: list[ContractChoice] = []
    rejection_reasons: dict[str, int] = {}
    for raw in contracts:
        if str(getattr(raw, "option_type", "") or "").upper() != side:
            continue
        choice, reason = _quality(raw, max_quote_age_seconds=max_quote_age_seconds, now=now)
        if choice is None:
            rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
            continue
        valid.append(choice)

    if not valid:
        detail = ",".join(f"{key}:{value}" for key, value in sorted(rejection_reasons.items()))
        return PolicyDecision(
            "DATA_INVALID",
            "no_liquid_contract" + (f"[{detail}]" if detail else ""),
        )

    spot = _num(underlying_price)

    def rank(choice: ContractChoice) -> tuple[float, float, float]:
        delta_rank = (
            abs(abs(choice.delta) - DELTA_TARGET)
            if choice.delta is not None
            else 9.0
        )
        if spot is None:
            strike_rank = 0.0
        elif side == "CALL":
            strike_rank = abs(choice.strike - spot) + (0.0 if choice.strike >= spot else 10_000.0)
        else:
            strike_rank = abs(choice.strike - spot) + (0.0 if choice.strike <= spot else 10_000.0)
        return (delta_rank, choice.spread_percent, strike_rank)

    selected = min(valid, key=rank)
    return PolicyDecision("VALID", contract=selected)


def build_spxw_contract_fields(
    *,
    expiry: ExpiryChoice,
    contract: ContractChoice,
    underlying_invalidation: Any,
    target_1: Any,
    aggregate_open_risk: float,
) -> tuple[dict[str, Any] | None, str]:
    stop_underlying = _num(underlying_invalidation)
    target = _num(target_1)
    if stop_underlying is None:
        return None, "underlying_invalidation_missing"
    if target is None:
        return None, "target_missing"

    entry = contract.ask
    premium_stop = round(entry * PREMIUM_STOP_MULTIPLIER, 4)
    planned_risk = round((entry - premium_stop) * CONTRACT_MULTIPLIER, 2)
    if planned_risk <= 0 or planned_risk > MAX_TRADE_RISK_DOLLARS:
        return None, f"planned_risk_outside_spxw_cap:{planned_risk:.2f}"

    projected = round(float(aggregate_open_risk) + planned_risk, 2)
    if projected > MAX_AGGREGATE_OPEN_RISK_DOLLARS:
        return None, f"aggregate_risk_cap_exceeded:{projected:.2f}"

    spread_cost = round((contract.ask - contract.bid) * CONTRACT_MULTIPLIER, 2)
    warnings = [item for item in (expiry.warning, contract.warning) if item]
    return (
        {
            "paper_policy_id": POLICY_ID,
            "paper_policy_status": "VALID",
            "paper_policy_warnings": warnings,
            "signal_underlying": SIGNAL_UNDERLYING,
            "contract_root": CONTRACT_ROOT,
            "settlement": SETTLEMENT,
            "dte_cohort": expiry.cohort,
            "contract": contract.symbol,
            "option_type": contract.option_type,
            "strike": contract.strike,
            "expiry": expiry.expiration,
            "dte": expiry.dte,
            "dte_bucket": expiry.bucket,
            "option_bid": contract.bid,
            "option_ask": contract.ask,
            "option_mark": entry,
            "entry_mark_basis": "ASK",
            "spread_percent": round(contract.spread_percent, 4),
            "spread_cost_dollars": spread_cost,
            "option_volume": contract.volume,
            "open_interest": contract.open_interest,
            "delta": contract.delta,
            "gamma": contract.gamma,
            "theta": contract.theta,
            "implied_volatility": contract.implied_volatility,
            "option_quote_timestamp": contract.quote_timestamp,
            "option_quote_source": contract.quote_source,
            "premium_stop": premium_stop,
            "premium_stop_adverse_percent": PREMIUM_STOP_ADVERSE_PERCENT,
            "planned_risk_dollars": planned_risk,
            "risk_cap": planned_risk,
            "contracts": 1,
            "contract_multiplier": CONTRACT_MULTIPLIER,
            "aggregate_open_planned_risk_before": round(float(aggregate_open_risk), 2),
            "projected_aggregate_open_planned_risk": projected,
            "max_trade_planned_risk": MAX_TRADE_RISK_DOLLARS,
            "max_aggregate_open_planned_risk": MAX_AGGREGATE_OPEN_RISK_DOLLARS,
            "cost_model": "entry_at_ask_exit_at_bid_no_commission",
            "underlying_invalidation": stop_underlying,
            "target_1": target,
            "averaging_down": False,
        },
        "",
    )


def data_invalid(reason: str) -> dict[str, Any]:
    return {
        "paper_policy_id": POLICY_ID,
        "paper_policy_status": "DATA_INVALID",
        "paper_policy_reason": reason,
        "signal_underlying": SIGNAL_UNDERLYING,
        "contract_root": CONTRACT_ROOT,
    }


def entry_late(reason: str) -> dict[str, Any]:
    return {
        "paper_policy_id": POLICY_ID,
        "paper_policy_status": ENTRY_LATE_STATUS,
        "paper_policy_reason": reason,
        "signal_underlying": SIGNAL_UNDERLYING,
        "contract_root": CONTRACT_ROOT,
    }


def evaluate_entry_gates(
    *,
    setup_status: str,
    direction: str,
    price: float | None,
    invalidation: Any,
    target_1: Any,
    min_remaining_rr: float = DEFAULT_MIN_REMAINING_RR,
) -> dict[str, Any] | None:
    """Return a fail-closed policy overlay, or None when entry may proceed."""
    if str(setup_status or "").upper() != "TRIGGERED":
        return data_invalid("setup_not_triggered")
    if direction not in {"LONG", "SHORT"}:
        return data_invalid("direction_unknown")
    if invalidation in (None, ""):
        return data_invalid("underlying_invalidation_missing")
    if target_1 in (None, ""):
        return data_invalid("target_missing")
    late = entry_late_reason(
        direction,
        price,
        _num(invalidation),
        _num(target_1),
        min_remaining_rr,
    )
    if late:
        return entry_late(late)
    return None


# Re-export helpers used by the lane so callers need not import paper_v1 for SPXW.
__all__ = [
    "POLICY_ID",
    "SIGNAL_UNDERLYING",
    "CONTRACT_ROOT",
    "CONTRACT_MULTIPLIER",
    "COHORT_0DTE",
    "COHORT_1_PLUS",
    "ExpiryChoice",
    "ContractChoice",
    "PolicyDecision",
    "classify_dte_cohort",
    "choose_expiration",
    "list_eligible_expirations",
    "choose_contract",
    "build_spxw_contract_fields",
    "data_invalid",
    "entry_late",
    "evaluate_entry_gates",
    "entry_late_reason",
    "entry_geometry_state",
    "remaining_reward_to_risk",
    "DEFAULT_MIN_REMAINING_RR",
]
