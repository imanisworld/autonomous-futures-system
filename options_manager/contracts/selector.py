"""Deterministic, advisory-only option contract selector.

Pure selection logic over a caller-supplied decision-time chain snapshot.
No I/O, no provider fetch, no broker call, no order construction, and no
runtime activation. The selector is intentionally separate from
contract_validator.evaluate_contract_constraints(): selection chooses one
eligible contract; validation independently evaluates the chosen contract.

The function is fail-closed. Missing/invalid fields, future-dated quotes,
illiquidity, wide spreads, and DTE/delta violations exclude a row. If no
row survives, the result is NO_CONTRACT.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import math
from typing import Literal, Mapping, Sequence

OptionRight = Literal["CALL", "PUT"]
SelectionStatus = Literal["SELECTED", "NO_CONTRACT"]


@dataclass(frozen=True)
class SelectorRule:
    rule_id: str
    min_dte: int
    preferred_min_dte: int
    min_abs_delta: float
    max_abs_delta: float
    target_abs_delta: float
    max_spread_percent: float
    min_volume: int
    min_open_interest: int


@dataclass(frozen=True)
class OptionChainRow:
    contract_id: str
    expiration: str
    strike: float
    right: OptionRight
    bid: float
    ask: float
    quote_ts: str
    volume: int
    open_interest: int
    delta: float
    dte: int


@dataclass(frozen=True)
class ContractSelectionResult:
    status: SelectionStatus
    reason_code: str
    rule_id: str
    contract_id: str | None = None
    expiration: str | None = None
    strike: float | None = None
    right: OptionRight | None = None
    dte: int | None = None
    delta: float | None = None
    spread_percent: float | None = None
    selection_reason: str | None = None
    candidates_considered: int = 0
    candidates_excluded_by_reason: Mapping[str, int] = field(default_factory=dict)


def selector_rule_from_mapping(raw: Mapping[str, object]) -> SelectorRule:
    """Build a validated SelectorRule from a decoded frozen rule mapping."""

    required = (
        "rule_id",
        "min_dte",
        "preferred_min_dte",
        "min_abs_delta",
        "max_abs_delta",
        "target_abs_delta",
        "max_spread_percent",
        "min_volume",
        "min_open_interest",
    )
    missing = [key for key in required if key not in raw]
    if missing:
        raise ValueError(f"missing selector rule fields: {','.join(sorted(missing))}")

    if not isinstance(raw["rule_id"], str) or not raw["rule_id"].strip():
        raise ValueError("rule_id must be a non-empty string")

    def number(name: str) -> float:
        value = raw[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be numeric")
        value = float(value)
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
        return value

    def integer(name: str) -> int:
        value = raw[name]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} must be an integer")
        return value

    rule = SelectorRule(
        rule_id=raw["rule_id"].strip(),
        min_dte=integer("min_dte"),
        preferred_min_dte=integer("preferred_min_dte"),
        min_abs_delta=number("min_abs_delta"),
        max_abs_delta=number("max_abs_delta"),
        target_abs_delta=number("target_abs_delta"),
        max_spread_percent=number("max_spread_percent"),
        min_volume=integer("min_volume"),
        min_open_interest=integer("min_open_interest"),
    )

    if rule.min_dte < 1:
        raise ValueError("min_dte must be >= 1")
    if rule.preferred_min_dte < rule.min_dte:
        raise ValueError("preferred_min_dte must be >= min_dte")
    if not (0 < rule.min_abs_delta <= rule.target_abs_delta <= rule.max_abs_delta <= 1):
        raise ValueError("delta bounds must satisfy 0 < min <= target <= max <= 1")
    if rule.max_spread_percent <= 0:
        raise ValueError("max_spread_percent must be > 0")
    if rule.min_volume < 0 or rule.min_open_interest < 0:
        raise ValueError("liquidity minima must be >= 0")

    return rule


def _parse_ts(value: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp must be a non-empty string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed


def _finite_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _spread_percent(bid: float, ask: float) -> float:
    mid = (bid + ask) / 2.0
    return ((ask - bid) / mid) * 100.0


def select_contract(
    *,
    rule: SelectorRule,
    decision_ts: str,
    underlying_price: float,
    direction: OptionRight,
    chain: Sequence[OptionChainRow],
) -> ContractSelectionResult:
    """Select one contract deterministically from information known at decision_ts."""

    if direction not in ("CALL", "PUT"):
        return ContractSelectionResult(
            status="NO_CONTRACT",
            reason_code="invalid_direction",
            rule_id=rule.rule_id,
        )

    if not _finite_number(underlying_price) or float(underlying_price) <= 0:
        return ContractSelectionResult(
            status="NO_CONTRACT",
            reason_code="invalid_underlying_price",
            rule_id=rule.rule_id,
        )

    try:
        decision_dt = _parse_ts(decision_ts)
    except (TypeError, ValueError):
        return ContractSelectionResult(
            status="NO_CONTRACT",
            reason_code="invalid_decision_timestamp",
            rule_id=rule.rule_id,
        )

    excluded: dict[str, int] = {}
    eligible: list[tuple[tuple[object, ...], OptionChainRow, float]] = []

    def reject(reason: str) -> None:
        excluded[reason] = excluded.get(reason, 0) + 1

    for row in chain:
        if not row.contract_id or not row.expiration:
            reject("missing_identity")
            continue
        if row.right != direction:
            reject("wrong_right")
            continue

        try:
            quote_dt = _parse_ts(row.quote_ts)
        except (TypeError, ValueError):
            reject("invalid_quote_timestamp")
            continue
        if quote_dt > decision_dt:
            reject("future_quote")
            continue

        numeric_values = (
            row.strike,
            row.bid,
            row.ask,
            row.delta,
        )
        if not all(_finite_number(v) for v in numeric_values):
            reject("invalid_numeric_field")
            continue
        if isinstance(row.dte, bool) or not isinstance(row.dte, int):
            reject("invalid_dte")
            continue
        if isinstance(row.volume, bool) or not isinstance(row.volume, int):
            reject("invalid_volume")
            continue
        if isinstance(row.open_interest, bool) or not isinstance(row.open_interest, int):
            reject("invalid_open_interest")
            continue

        strike = float(row.strike)
        bid = float(row.bid)
        ask = float(row.ask)
        abs_delta = abs(float(row.delta))

        if strike <= 0:
            reject("invalid_strike")
            continue
        if bid <= 0 or ask <= bid:
            reject("invalid_bid_ask")
            continue
        if row.dte < rule.min_dte:
            reject("dte_too_short")
            continue
        if row.volume < rule.min_volume:
            reject("volume_too_low")
            continue
        if row.open_interest < rule.min_open_interest:
            reject("open_interest_too_low")
            continue
        if abs_delta < rule.min_abs_delta or abs_delta > rule.max_abs_delta:
            reject("delta_out_of_range")
            continue

        spread_percent = _spread_percent(bid, ask)
        if spread_percent > rule.max_spread_percent:
            reject("spread_too_wide")
            continue

        preferred_bucket = 0 if row.dte >= rule.preferred_min_dte else 1
        delta_distance = abs(abs_delta - rule.target_abs_delta)
        moneyness_distance = abs(strike - float(underlying_price)) / float(underlying_price)

        rank = (
            preferred_bucket,
            delta_distance,
            moneyness_distance,
            row.dte,
            spread_percent,
            -row.open_interest,
            -row.volume,
            row.expiration,
            strike,
            row.contract_id,
        )
        eligible.append((rank, row, spread_percent))

    if not eligible:
        return ContractSelectionResult(
            status="NO_CONTRACT",
            reason_code="no_eligible_contract",
            rule_id=rule.rule_id,
            candidates_considered=len(chain),
            candidates_excluded_by_reason=dict(sorted(excluded.items())),
        )

    _, selected, spread_percent = min(eligible, key=lambda item: item[0])
    preferred = selected.dte >= rule.preferred_min_dte
    selection_reason = (
        "preferred_dte_delta_liquidity_rank"
        if preferred
        else "fallback_min_dte_delta_liquidity_rank"
    )

    return ContractSelectionResult(
        status="SELECTED",
        reason_code="contract_selected",
        rule_id=rule.rule_id,
        contract_id=selected.contract_id,
        expiration=selected.expiration,
        strike=float(selected.strike),
        right=selected.right,
        dte=selected.dte,
        delta=float(selected.delta),
        spread_percent=spread_percent,
        selection_reason=selection_reason,
        candidates_considered=len(chain),
        candidates_excluded_by_reason=dict(sorted(excluded.items())),
    )
