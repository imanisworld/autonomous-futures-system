"""Advisory-only contract quality gate for options_manager.

The gate validates a caller-supplied option snapshot. It performs no I/O and
never fetches market data, touches a broker, or submits an order.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, Mapping, Optional

DEFAULT_MAX_PREMIUM_DOLLARS = 300.0
DEFAULT_MAX_DOLLAR_RISK = 300.0
DEFAULT_MIN_DTE = 14
DEFAULT_PREFERRED_SWING_DTE = 45
DEFAULT_CONTRACT_MULTIPLIER = 100
DEFAULT_MAX_SPREAD_PERCENT = 10.0
DEFAULT_MIN_VOLUME = 100
DEFAULT_MIN_OPEN_INTEREST = 500
DEFAULT_MIN_DISTANCE_TO_TARGET_PERCENT = 2.0

def _is_finite_number(value: object) -> bool:
    """True only for a real, finite int/float (bool excluded). Kept
    import-free on purpose: this module's import surface is asserted by
    tests to stay within options_manager and the stdlib names already used."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return value == value and value not in (float("inf"), float("-inf"))


_NUMERIC_CONTRACT_FIELDS = (
    "strike",
    "premium",
    "bid",
    "ask",
    "spread_percent",
    "volume",
    "open_interest",
    "dte",
    "max_contracts",
    "max_dollar_risk",
    "distance_to_target",
    "premium_stop",
)
_RiskSeverity = Literal["none", "low", "moderate", "high"]
_VALID_SEVERITIES = ("none", "low", "moderate", "high")
_TradeStyle = Literal["swing", "intraday"]
_VALID_TRADE_STYLES = ("swing", "intraday")


class GateVerdict(str, Enum):
    PASS = "pass"
    WARN = "warn"
    BLOCK = "block"


@dataclass(frozen=True, kw_only=True)
class ContractQualityInput:
    ticker: str
    direction: Literal["CALL", "PUT"]
    expiration: str
    strike: float
    premium: float
    bid: float
    ask: float
    spread_percent: float
    volume: int
    open_interest: int
    dte: int
    max_contracts: int
    max_dollar_risk: float
    distance_to_target: float
    iv_event_risk: _RiskSeverity
    theta_risk: _RiskSeverity
    notes: str = ""
    dte_exceptional: bool = False
    premium_risk_accepted: bool = False
    premium_stop: Optional[float] = None
    trade_style: _TradeStyle = "swing"


@dataclass(frozen=True, kw_only=True)
class ContractQualityResult:
    verdict: GateVerdict
    blocking_reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    contract: Optional[ContractQualityInput] = None


def evaluate_contract_quality(contract: ContractQualityInput) -> ContractQualityResult:
    blocking: list[str] = []
    warnings: list[str] = []

    for name in _NUMERIC_CONTRACT_FIELDS:
        value = getattr(contract, name)
        if value is None:
            continue
        if not _is_finite_number(value):
            blocking.append(f"non-finite {name}")

    if not contract.ticker.strip():
        blocking.append("missing ticker")
    if not contract.expiration.strip():
        blocking.append("missing expiration")
    if contract.strike <= 0:
        blocking.append("missing/invalid strike")
    if contract.max_contracts <= 0:
        blocking.append("missing/invalid max_contracts")

    if contract.premium <= 0:
        blocking.append("missing/invalid premium")
    premium_dollars_per_contract = contract.premium * DEFAULT_CONTRACT_MULTIPLIER

    planned_dollar_risk: Optional[float] = None
    if contract.premium_stop is not None:
        if contract.premium_stop < 0 or contract.premium_stop >= contract.premium:
            blocking.append("missing/invalid premium_stop")
        else:
            planned_dollar_risk = (
                (contract.premium - contract.premium_stop)
                * DEFAULT_CONTRACT_MULTIPLIER
                * contract.max_contracts
            )

    if contract.bid <= 0:
        blocking.append("missing bid")
    if contract.ask <= 0:
        blocking.append("missing ask")
    if contract.spread_percent < 0:
        blocking.append("missing/invalid spread_percent")
    elif contract.spread_percent > DEFAULT_MAX_SPREAD_PERCENT:
        blocking.append(
            f"spread too wide ({contract.spread_percent:.1f}% > "
            f"{DEFAULT_MAX_SPREAD_PERCENT:.1f}%)"
        )

    if contract.volume <= 0:
        blocking.append("missing/invalid volume")
    elif contract.volume < DEFAULT_MIN_VOLUME:
        blocking.append(f"low volume ({contract.volume} < {DEFAULT_MIN_VOLUME})")

    if contract.open_interest <= 0:
        blocking.append("missing/invalid open_interest")
    elif contract.open_interest < DEFAULT_MIN_OPEN_INTEREST:
        blocking.append(
            f"low open interest ({contract.open_interest} < {DEFAULT_MIN_OPEN_INTEREST})"
        )

    if contract.dte < 0:
        blocking.append("missing/invalid dte")
    elif contract.dte == 0 and not contract.dte_exceptional:
        blocking.append("missing/invalid dte: 0 DTE requires explicit exception")
    elif contract.dte < DEFAULT_MIN_DTE:
        if contract.dte_exceptional:
            warnings.append(
                f"DTE below preferred minimum ({contract.dte}d < {DEFAULT_MIN_DTE}d), "
                "accepted as an explicit exception"
            )
        else:
            blocking.append(
                f"DTE too short ({contract.dte}d < {DEFAULT_MIN_DTE}d) and not marked exceptional"
            )
    elif contract.trade_style == "swing" and contract.dte < DEFAULT_PREFERRED_SWING_DTE:
        warnings.append(
            f"swing DTE below preferred {DEFAULT_PREFERRED_SWING_DTE}d "
            f"({contract.dte}d); contract is valid but not preferred swing duration"
        )

    if premium_dollars_per_contract > DEFAULT_MAX_PREMIUM_DOLLARS:
        if contract.premium_risk_accepted:
            warnings.append(
                f"premium above preferred cap (${premium_dollars_per_contract:.2f}/contract > "
                f"${DEFAULT_MAX_PREMIUM_DOLLARS:.2f}), risk explicitly accepted"
            )
        else:
            blocking.append(
                f"premium ${premium_dollars_per_contract:.2f}/contract exceeds "
                f"${DEFAULT_MAX_PREMIUM_DOLLARS:.2f} cap and risk not accepted"
            )

    if contract.max_dollar_risk <= 0:
        blocking.append("missing/invalid max_dollar_risk")
    elif contract.max_dollar_risk > DEFAULT_MAX_DOLLAR_RISK:
        blocking.append(
            f"max_dollar_risk ${contract.max_dollar_risk:.2f} exceeds plan cap "
            f"${DEFAULT_MAX_DOLLAR_RISK:.2f}"
        )
    elif planned_dollar_risk is not None and planned_dollar_risk > contract.max_dollar_risk:
        blocking.append(
            f"planned premium-stop risk ${planned_dollar_risk:.2f} exceeds stated "
            f"max_dollar_risk ${contract.max_dollar_risk:.2f}"
        )

    if contract.distance_to_target < DEFAULT_MIN_DISTANCE_TO_TARGET_PERCENT:
        blocking.append(
            f"distance_to_target {contract.distance_to_target:.1f}% is below the "
            f"{DEFAULT_MIN_DISTANCE_TO_TARGET_PERCENT:.1f}% minimum relative to the risk taken"
        )

    if contract.iv_event_risk == "high":
        blocking.append("IV/event risk is HIGH")
    elif contract.iv_event_risk == "moderate":
        warnings.append("IV/event risk is MODERATE")

    if contract.theta_risk == "high":
        blocking.append("theta risk is HIGH")
    elif contract.theta_risk == "moderate":
        warnings.append("theta risk is MODERATE")

    verdict = GateVerdict.BLOCK if blocking else GateVerdict.WARN if warnings else GateVerdict.PASS
    return ContractQualityResult(
        verdict=verdict,
        blocking_reasons=tuple(blocking),
        warnings=tuple(warnings),
        contract=contract,
    )


_STR_FIELDS = ("ticker", "expiration")
_FLOAT_FIELDS = (
    "strike",
    "premium",
    "bid",
    "ask",
    "spread_percent",
    "max_dollar_risk",
    "distance_to_target",
)
_INT_FIELDS = ("volume", "open_interest", "dte", "max_contracts")
_BOOL_FIELDS = ("dte_exceptional", "premium_risk_accepted")
_SEVERITY_FIELDS = ("iv_event_risk", "theta_risk")

_REQUIRED_FIELD_NAMES = tuple(
    f.name
    for f in dataclasses.fields(ContractQualityInput)
    if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
)


def _is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _coerce_direction(value: Any) -> str:
    text = str(value).strip().upper()
    if text not in ("CALL", "PUT"):
        raise ValueError(f"{value!r} is not CALL or PUT")
    return text


def _coerce_severity(value: Any) -> str:
    text = str(value).strip().lower()
    if text not in _VALID_SEVERITIES:
        raise ValueError(f"{value!r} is not one of {_VALID_SEVERITIES}")
    return text


def _coerce_trade_style(value: Any) -> str:
    text = str(value).strip().lower()
    if text not in _VALID_TRADE_STYLES:
        raise ValueError(f"{value!r} is not one of {_VALID_TRADE_STYLES}")
    return text


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "yes", "1"):
            return True
        if text in ("false", "no", "0"):
            return False
    raise ValueError(f"{value!r} is not a valid boolean")


def check_contract_quality_intake(payload: Any) -> ContractQualityResult:
    if not isinstance(payload, Mapping):
        return ContractQualityResult(
            verdict=GateVerdict.BLOCK,
            blocking_reasons=(
                f"malformed payload: expected a dict-like mapping, got {type(payload).__name__}",
            ),
        )

    missing = [name for name in _REQUIRED_FIELD_NAMES if not _is_present(payload.get(name))]
    if missing:
        return ContractQualityResult(
            verdict=GateVerdict.BLOCK,
            blocking_reasons=tuple(f"missing {name}" for name in missing),
        )

    coercion_errors: list[str] = []
    normalized: dict[str, Any] = {}

    for name in _REQUIRED_FIELD_NAMES:
        raw_value = payload[name]
        try:
            if name in _STR_FIELDS:
                normalized[name] = str(raw_value)
            elif name in _FLOAT_FIELDS:
                normalized[name] = float(raw_value)
            elif name in _INT_FIELDS:
                normalized[name] = int(raw_value)
            elif name == "direction":
                normalized[name] = _coerce_direction(raw_value)
            elif name in _SEVERITY_FIELDS:
                normalized[name] = _coerce_severity(raw_value)
            else:
                normalized[name] = raw_value
        except (TypeError, ValueError) as exc:
            coercion_errors.append(f"invalid value for {name}: {exc}")

    for name in _BOOL_FIELDS:
        if name in payload and payload[name] is not None:
            try:
                normalized[name] = _coerce_bool(payload[name])
            except ValueError as exc:
                coercion_errors.append(f"invalid value for {name}: {exc}")

    if "premium_stop" in payload and payload["premium_stop"] is not None:
        try:
            normalized["premium_stop"] = float(payload["premium_stop"])
        except (TypeError, ValueError) as exc:
            coercion_errors.append(f"invalid value for premium_stop: {exc}")

    if "trade_style" in payload and payload["trade_style"] is not None:
        try:
            normalized["trade_style"] = _coerce_trade_style(payload["trade_style"])
        except ValueError as exc:
            coercion_errors.append(f"invalid value for trade_style: {exc}")

    if "notes" in payload and payload["notes"] is not None:
        normalized["notes"] = str(payload["notes"])

    if coercion_errors:
        return ContractQualityResult(
            verdict=GateVerdict.BLOCK,
            blocking_reasons=tuple(coercion_errors),
        )

    contract = ContractQualityInput(**normalized)
    return evaluate_contract_quality(contract)
