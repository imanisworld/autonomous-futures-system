"""Frozen Options Paper Test V1 contract and risk policy.

This module is intentionally pure.  It selects a *real chain contract* from a
caller-supplied chain snapshot and applies the operator-approved paper-test
policy without any broker or order capability.

Policy V1 (frozen for the first evidence population):
- max planned risk per trade: $300
- max aggregate open planned risk: $1,000
- 45+ DTE preferred
- 14-44 DTE allowed and tagged DTE_EXCEPTION
- <14 DTE excluded
- premium stop: 25% adverse from entry premium (entry at ask)
- Signa and GEX are context only; neither is a hard approval gate here
- missing critical setup/quote/risk data => DATA_INVALID

The 25% premium stop is the explicit V1 numeric stop used for risk accounting.
Changing it, the DTE bands, or either dollar cap creates a new policy version and
must not be mixed into this evidence population.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite
from typing import Any, Iterable
from zoneinfo import ZoneInfo

POLICY_ID = "OPTIONS_PAPER_V1"
MAX_TRADE_RISK_DOLLARS = 300.0
MAX_AGGREGATE_OPEN_RISK_DOLLARS = 1_000.0
MIN_DTE = 14
PREFERRED_DTE = 45
# Sanity bound, not an optimization target.  It exists specifically so malformed
# fixture-like dates such as 2099 can never become an actionable contract.
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


@dataclass(frozen=True)
class ExpiryChoice:
    expiration: str
    dte: int
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


def dte_for(expiration: Any, now: datetime | date) -> int | None:
    expiry = _as_date(expiration)
    if expiry is None:
        return None
    today = now.date() if isinstance(now, datetime) else now
    return (expiry - today).days


def expiration_is_sane(expiration: Any, now: datetime | date) -> bool:
    dte = dte_for(expiration, now)
    return dte is not None and 0 <= dte <= MAX_SANITY_DTE


def choose_expiration(expirations: Iterable[Any], now: datetime) -> PolicyDecision:
    parsed: list[tuple[str, int]] = []
    for raw in expirations:
        expiry = _as_date(raw)
        if expiry is None:
            continue
        dte = (expiry - now.date()).days
        if MIN_DTE <= dte <= MAX_SANITY_DTE:
            parsed.append((expiry.isoformat(), dte))

    if not parsed:
        return PolicyDecision("DATA_INVALID", "no_expiration_in_v1_dte_range")

    preferred = sorted((row for row in parsed if row[1] >= PREFERRED_DTE), key=lambda row: row[1])
    if preferred:
        expiration, dte = preferred[0]
        return PolicyDecision(
            "VALID",
            expiry=ExpiryChoice(expiration=expiration, dte=dte, bucket="45_PLUS"),
        )

    # If no 45+ contract exists, use the contract closest to the preferred band
    # while preserving the operator-approved 14-44 DTE exception population.
    exception = max(parsed, key=lambda row: row[1])
    expiration, dte = exception
    return PolicyDecision(
        "VALID",
        expiry=ExpiryChoice(
            expiration=expiration,
            dte=dte,
            bucket="14_44",
            warning="DTE_EXCEPTION",
        ),
    )


def _num(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _quality(contract: Any) -> tuple[ContractChoice | None, str]:
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
            warning=warning,
        ),
        "",
    )


def choose_contract(
    contracts: Iterable[Any],
    *,
    option_type: str,
    underlying_price: float | None,
) -> PolicyDecision:
    side = option_type.upper()
    if side not in {"CALL", "PUT"}:
        return PolicyDecision("DATA_INVALID", "invalid_option_type")

    valid: list[ContractChoice] = []
    rejection_reasons: dict[str, int] = {}
    for raw in contracts:
        if str(getattr(raw, "option_type", "") or "").upper() != side:
            continue
        choice, reason = _quality(raw)
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


def build_v1_contract_fields(
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

    # Conservative paper entry: cross the spread at the ask.  This makes entry
    # friction explicit and avoids optimistic midpoint fills.
    entry = contract.ask
    premium_stop = round(entry * PREMIUM_STOP_MULTIPLIER, 4)
    planned_risk = round((entry - premium_stop) * CONTRACT_MULTIPLIER, 2)
    if planned_risk <= 0 or planned_risk > MAX_TRADE_RISK_DOLLARS:
        return None, f"planned_risk_outside_v1_cap:{planned_risk:.2f}"

    projected = round(float(aggregate_open_risk) + planned_risk, 2)
    if projected > MAX_AGGREGATE_OPEN_RISK_DOLLARS:
        return None, f"aggregate_risk_cap_exceeded:{projected:.2f}"

    warnings = [item for item in (expiry.warning, contract.warning) if item]
    return (
        {
            "paper_policy_id": POLICY_ID,
            "paper_policy_status": "VALID",
            "paper_policy_warnings": warnings,
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
            "option_volume": contract.volume,
            "open_interest": contract.open_interest,
            "delta": contract.delta,
            "gamma": contract.gamma,
            "theta": contract.theta,
            "implied_volatility": contract.implied_volatility,
            "option_quote_timestamp": contract.quote_timestamp,
            "premium_stop": premium_stop,
            "premium_stop_adverse_percent": PREMIUM_STOP_ADVERSE_PERCENT,
            "planned_risk_dollars": planned_risk,
            "risk_cap": planned_risk,
            "contracts": 1,
            "aggregate_open_planned_risk_before": round(float(aggregate_open_risk), 2),
            "projected_aggregate_open_planned_risk": projected,
            "max_trade_planned_risk": MAX_TRADE_RISK_DOLLARS,
            "max_aggregate_open_planned_risk": MAX_AGGREGATE_OPEN_RISK_DOLLARS,
            "cost_model": "entry_at_ask_exit_at_bid_no_commission",
            "underlying_invalidation": stop_underlying,
            "target_1": target,
        },
        "",
    )


def data_invalid(reason: str) -> dict[str, Any]:
    return {
        "paper_policy_id": POLICY_ID,
        "paper_policy_status": "DATA_INVALID",
        "paper_policy_reason": reason,
    }


EXCHANGE_TIMEZONE = "America/New_York"
ACTIVE_LANE = "ACTIVE"
_RTH_OPEN_MINUTES = 9 * 60 + 30
_RTH_HOUR_MINUTES = 60
_RTH_BLOCK_MINUTES = 240


def episode_bucket(timeframe: str | None, moment: datetime) -> str:
    """Bucket a scan timestamp into one bar of the setup's OWN timeframe.

    Evidence accounting rule: one distinct setup is one evidence episode.  The
    scanner re-evaluates every interval, so without this bucket a setup that
    stays valid across a bar is journalled once per tick and inflates ``n`` by
    the scan rate rather than by the number of distinct setups.
    """
    local = moment.astimezone(ZoneInfo(EXCHANGE_TIMEZONE))
    label = str(timeframe or "").strip().upper()
    day = local.date().isoformat()
    if label == "1D":
        return f"1D:{day}"
    if label == "4H_RTH":
        offset = (local.hour * 60 + local.minute) - _RTH_OPEN_MINUTES
        block = offset // _RTH_BLOCK_MINUTES if offset >= 0 else -1
        return f"4H_RTH:{day}:{block}"
    if label == "1H":
        # 1H evidence candles are built by _timeframe_series anchored to the
        # RTH session open (9:30-10:30, 10:30-11:30, ...), never to the clock
        # hour.  Bucket the same way or one setup straddles two candles and a
        # genuinely new 10:30 setup is suppressed until 11:00.
        offset = (local.hour * 60 + local.minute) - _RTH_OPEN_MINUTES
        block = offset // _RTH_HOUR_MINUTES if offset >= 0 else -1
        return f"1H:{day}:{block}"
    # 30m and any unmapped timeframe fall back to the 30m grid.  The label stays
    # in the key so two unmapped timeframes can never share an episode.
    half = 0 if local.minute < 30 else 30
    return f"{label or '30M'}:{day}:{local.hour:02d}:{half:02d}"


def _canonical_trigger(trigger: object) -> str:
    try:
        value = float(trigger)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "NA"
    if value != value:  # NaN
        return "NA"
    return f"{value:.4f}"


def setup_episode_key(
    *,
    ticker: str,
    lane: str | None,
    timeframe: str | None,
    setup_type: str | None,
    direction: str | None,
    trigger: object,
    moment: datetime,
    legacy_candidate_key: str | None = None,
) -> str:
    """Identity of one evidence episode: the UNDERLYING setup within one of its
    own bars.

    The selected option contract is deliberately NOT part of this identity.
    Delta-targeted contract selection drifts with the underlying (2026-09-09:
    the same AAPL Daily 2-2-2 continuation, same trigger 314.90, same trading
    date, was journalled twice as 305P then 310P), so a contract-keyed episode
    re-counts one setup every time the strike moves.  The contract-specific
    candidate key stays on the row for exact-contract tracking and requotes.

    The evidence lane stays in the key so an ACTIVE setup can never be deduped
    against a COUNTERFACTUAL observation of the same structure.

    Legacy candidates (webhook payloads carrying no ``setup_type`` and no
    ``setup_timeframe``) have no underlying-setup identity to key on, so they
    keep the contract-keyed candidate identity they always had.  Every V1
    population carries all three setup fields and never takes this branch.
    """
    label_timeframe = str(timeframe or "").strip().upper()
    label_setup = str(setup_type or "").strip().upper()
    if not label_timeframe and not label_setup and legacy_candidate_key:
        return (
            f"{str(ticker or '').strip().upper() or 'UNSPECIFIED'}|"
            f"{str(lane or ACTIVE_LANE).strip().upper()}|LEGACY|{legacy_candidate_key}"
            f"@{episode_bucket(timeframe, moment)}"
        )
    parts = (
        str(ticker or "").strip().upper() or "UNSPECIFIED",
        str(lane or ACTIVE_LANE).strip().upper(),
        str(timeframe or "").strip().upper() or "UNSPECIFIED",
        str(setup_type or "").strip().upper() or "UNSPECIFIED",
        str(direction or "").strip().upper() or "UNSPECIFIED",
        _canonical_trigger(trigger),
    )
    return "|".join(parts) + f"@{episode_bucket(timeframe, moment)}"
