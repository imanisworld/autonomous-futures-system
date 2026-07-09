"""options_manager/validation/contract_quality_gate.py

Contract quality gate -- Increment 25L. A `ProofPacket` (Increment 25I)
proves a *setup* has a real trigger, invalidation, and targets. It says
nothing about whether the specific contract behind that setup is
actually tradable: a good chart with a wide-spread, illiquid, over-
expensive, or too-short-dated contract is still not a trade. This module
is a standalone advisory gate for that second question, deliberately
separate from `ProofPacket` -- a contract's own quality does not depend
on whether its setup is any good, and vice versa.

`evaluate_contract_quality()` takes an already-typed `ContractQualityInput`
and returns a `ContractQualityResult` with a `GateVerdict` of `PASS`,
`WARN`, or `BLOCK`, plus the specific `blocking_reasons`/`warnings` that
produced it. `check_contract_quality_intake()` is the manual-payload
entry point (a loose dict, typed in by hand) that normalizes into a
`ContractQualityInput` and runs the same evaluation -- never raising,
regardless of how malformed the payload is, the same non-throwing
pattern `check_proof_packet_intake()` established in Increment 25J.

Default thresholds (all named constants, all overridable per call via
`ContractQualityInput`'s own fields, never hidden inside the logic):
- preferred max premium: $300 (`DEFAULT_MAX_PREMIUM_DOLLARS`) -- blocks
  unless `premium_risk_accepted=True`.
- preferred max loss per trade: $300 (`DEFAULT_MAX_DOLLAR_RISK`) -- no
  override; a risk plan cap is not meant to be waved through.
- avoid weeklies: minimum 14 DTE (`DEFAULT_MIN_DTE`) -- blocks unless
  `dte_exceptional=True`.
- avoid wide spreads: max 10% (`DEFAULT_MAX_SPREAD_PERCENT`).
- avoid low liquidity: minimum 100 volume (`DEFAULT_MIN_VOLUME`),
  minimum 500 open interest (`DEFAULT_MIN_OPEN_INTEREST`).
- minimum reward distance: 2% (`DEFAULT_MIN_DISTANCE_TO_TARGET_PERCENT`)
  -- a target too close to spot relative to the risk taken blocks.
- IV/event risk and theta risk are each a `"none"|"low"|"moderate"|
  "high"` severity: `"moderate"` warns, `"high"` blocks.

This is advisory only -- it never fetches a quote, a candle, an option
chain, or a broker order, and it never reads the system clock. It never
places an order, changes a scanner setting, or promotes anything to
`FixtureStatus.CLEAN_COMPLETE_FIXTURE`. Performs no I/O of any kind: no
candle fetch, no option-chain fetch, no market-data fetch, no broker
call, no order placement, no execution, no alert sending, no file access
at runtime, no network calls, no MCP calls, no system-clock reads. Does
not import replay/replay_engine.py, the live context.market_context
loader, alert_ranker, options_companion, execution, webhook, broker
systems, options_manager.scanner, or risk/risk_engine.py.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, Mapping, Optional

DEFAULT_MAX_PREMIUM_DOLLARS = 300.0
DEFAULT_MAX_DOLLAR_RISK = 300.0
DEFAULT_MIN_DTE = 14
DEFAULT_MAX_SPREAD_PERCENT = 10.0
DEFAULT_MIN_VOLUME = 100
DEFAULT_MIN_OPEN_INTEREST = 500
DEFAULT_MIN_DISTANCE_TO_TARGET_PERCENT = 2.0

_RiskSeverity = Literal["none", "low", "moderate", "high"]
_VALID_SEVERITIES = ("none", "low", "moderate", "high")


class GateVerdict(str, Enum):
    """A contract quality check's overall outcome."""

    PASS = "pass"
    WARN = "warn"
    BLOCK = "block"


@dataclass(frozen=True, kw_only=True)
class ContractQualityInput:
    """One contract's quality-relevant facts, entirely as reported by
    the human filling this out -- nothing here is fetched from a quote
    or option chain. `dte_exceptional` and `premium_risk_accepted` are
    explicit human overrides for the two thresholds that are allowed
    one: a deliberately-chosen short-DTE or expensive contract is not an
    error, but it must be a stated choice, not a silent pass."""

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


@dataclass(frozen=True, kw_only=True)
class ContractQualityResult:
    """Outcome of a contract quality check. `contract` is populated only
    when `check_contract_quality_intake()` normalized a payload cleanly;
    `evaluate_contract_quality()` callers already have their own
    `ContractQualityInput` and can ignore it."""

    verdict: GateVerdict
    blocking_reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    contract: Optional[ContractQualityInput] = None


def evaluate_contract_quality(contract: ContractQualityInput) -> ContractQualityResult:
    """Runs every quality check against an already-typed
    `ContractQualityInput`. Verdict is `BLOCK` if any blocking reason
    fired, else `WARN` if any warning fired, else `PASS`. Every check
    runs regardless of earlier failures -- never partially reported."""
    blocking: list[str] = []
    warnings: list[str] = []

    if not contract.ticker.strip():
        blocking.append("missing ticker")
    if not contract.expiration.strip():
        blocking.append("missing expiration")
    if contract.strike <= 0:
        blocking.append("missing/invalid strike")
    if contract.max_contracts <= 0:
        blocking.append("missing/invalid max_contracts")

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

    if contract.dte <= 0:
        blocking.append("missing/invalid dte")
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

    if contract.premium > DEFAULT_MAX_PREMIUM_DOLLARS:
        if contract.premium_risk_accepted:
            warnings.append(
                f"premium above preferred cap (${contract.premium:.2f} > "
                f"${DEFAULT_MAX_PREMIUM_DOLLARS:.2f}), risk explicitly accepted"
            )
        else:
            blocking.append(
                f"premium ${contract.premium:.2f} exceeds ${DEFAULT_MAX_PREMIUM_DOLLARS:.2f} cap "
                "and risk not accepted"
            )

    if contract.max_dollar_risk > DEFAULT_MAX_DOLLAR_RISK:
        blocking.append(
            f"max_dollar_risk ${contract.max_dollar_risk:.2f} exceeds plan cap "
            f"${DEFAULT_MAX_DOLLAR_RISK:.2f}"
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

    if blocking:
        verdict = GateVerdict.BLOCK
    elif warnings:
        verdict = GateVerdict.WARN
    else:
        verdict = GateVerdict.PASS

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
    """Normalizes a manual dict-like payload into a `ContractQualityInput`
    and evaluates it with `evaluate_contract_quality()`. Never raises
    regardless of how malformed `payload` is -- a malformed payload or an
    uncoercible field value returns a `BLOCK` result naming the problem,
    with `contract=None`, instead of throwing."""
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
            else:  # pragma: no cover - defensive, every required field is classified above
                normalized[name] = raw_value
        except (TypeError, ValueError) as exc:
            coercion_errors.append(f"invalid value for {name}: {exc}")

    for name in _BOOL_FIELDS:
        if name in payload and payload[name] is not None:
            try:
                normalized[name] = _coerce_bool(payload[name])
            except ValueError as exc:
                coercion_errors.append(f"invalid value for {name}: {exc}")

    if "notes" in payload and payload["notes"] is not None:
        normalized["notes"] = str(payload["notes"])

    if coercion_errors:
        return ContractQualityResult(
            verdict=GateVerdict.BLOCK,
            blocking_reasons=tuple(coercion_errors),
        )

    contract = ContractQualityInput(**normalized)
    return evaluate_contract_quality(contract)
