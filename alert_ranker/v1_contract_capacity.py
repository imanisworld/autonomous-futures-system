"""Read-only V1 option-contract viability/capacity census.

This module mirrors the frozen ``OPTIONS_PAPER_V1`` expiration and contract
selection rules over a caller-supplied read-only market-data provider.  It does
not scan setups, write V1 evidence, send alerts, or expose broker/order paths.

Two different constraints are intentionally reported separately:

* ``v1_policy_ok`` mirrors the frozen V1 paper policy.  Because V1 books a 25%
  premium stop, its $300 max planned loss implies a selected ask <= $12.00.
* ``preferred_cost_ok`` is the operator's separate preference to avoid paying
  more than $300 total premium for one contract (ask <= $3.00).  It is census
  telemetry only and MUST NOT be confused with the frozen V1 risk rule.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Callable, Sequence

from .paper_v1 import (
    CONTRACT_MULTIPLIER,
    MAX_TRADE_RISK_DOLLARS,
    PREMIUM_STOP_ADVERSE_PERCENT,
    choose_contract,
    choose_expiration,
)

PREFERRED_MAX_CONTRACT_COST_DOLLARS = 300.0
_OPERATIONAL_ERROR_MARKERS = (
    "rate_limited",
    "timeout",
    "authentication_failed",
    "credentials_missing",
    "account_id_missing",
    "network_error",
    "http_status_",
)


@dataclass(frozen=True)
class SideContractResult:
    side: str
    v1_policy_ok: bool
    reason: str | None
    contract_symbol: str | None
    strike: float | None
    bid: float | None
    ask: float | None
    spread_percent: float | None
    volume: float | None
    open_interest: float | None
    delta: float | None
    contract_cost_dollars: float | None
    planned_risk_dollars: float | None
    preferred_cost_ok: bool | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SymbolContractResult:
    ticker: str
    elapsed_seconds: float
    expiration: str | None
    dte: int | None
    dte_bucket: str | None
    expiration_warning: str | None
    chain_error: str | None
    operational_error: bool
    call: SideContractResult
    put: SideContractResult

    @property
    def viable_sides(self) -> int:
        return int(self.call.v1_policy_ok) + int(self.put.v1_policy_ok)

    @property
    def preferred_cost_viable_sides(self) -> int:
        return int(self.call.v1_policy_ok and self.call.preferred_cost_ok is True) + int(
            self.put.v1_policy_ok and self.put.preferred_cost_ok is True
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["viable_sides"] = self.viable_sides
        payload["preferred_cost_viable_sides"] = self.preferred_cost_viable_sides
        return payload


@dataclass(frozen=True)
class ContractCensusReport:
    verdict: str
    reasons: tuple[str, ...]
    candidate_count: int
    tested_count: int
    total_elapsed_seconds: float
    operational_failures: int
    symbols_v1_both_sides: tuple[str, ...]
    symbols_v1_any_side: tuple[str, ...]
    symbols_preferred_cost_both_sides: tuple[str, ...]
    symbols_preferred_cost_any_side: tuple[str, ...]
    symbols_no_v1_contract: tuple[str, ...]
    contract_fetches: int
    storage_writes: int
    alerts_sent: int
    live_watchlist_changed: bool
    results: tuple[SymbolContractResult, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["results"] = [item.to_dict() for item in self.results]
        return payload


def _empty_side(side: str, reason: str) -> SideContractResult:
    return SideContractResult(
        side=side,
        v1_policy_ok=False,
        reason=reason,
        contract_symbol=None,
        strike=None,
        bid=None,
        ask=None,
        spread_percent=None,
        volume=None,
        open_interest=None,
        delta=None,
        contract_cost_dollars=None,
        planned_risk_dollars=None,
        preferred_cost_ok=None,
    )


def _side_result(
    chain: Any,
    *,
    side: str,
    underlying_price: float | None,
    preferred_max_contract_cost_dollars: float,
) -> SideContractResult:
    contracts = chain.calls if side == "CALL" else chain.puts
    decision = choose_contract(
        contracts,
        option_type=side,
        underlying_price=underlying_price,
    )
    if not decision.valid or decision.contract is None:
        return _empty_side(side, decision.reason or "no_liquid_contract")

    contract = decision.contract
    contract_cost = round(contract.ask * CONTRACT_MULTIPLIER, 2)
    planned_risk = round(
        contract.ask * (PREMIUM_STOP_ADVERSE_PERCENT / 100.0) * CONTRACT_MULTIPLIER,
        2,
    )
    risk_ok = 0 < planned_risk <= MAX_TRADE_RISK_DOLLARS
    return SideContractResult(
        side=side,
        v1_policy_ok=risk_ok,
        reason=None if risk_ok else f"planned_risk_outside_v1_cap:{planned_risk:.2f}",
        contract_symbol=contract.symbol,
        strike=contract.strike,
        bid=contract.bid,
        ask=contract.ask,
        spread_percent=round(contract.spread_percent, 4),
        volume=contract.volume,
        open_interest=contract.open_interest,
        delta=contract.delta,
        contract_cost_dollars=contract_cost,
        planned_risk_dollars=planned_risk,
        preferred_cost_ok=contract_cost <= preferred_max_contract_cost_dollars,
    )


def _operational_error(error: str | None) -> bool:
    text = str(error or "").strip().lower()
    return bool(text) and any(marker in text for marker in _OPERATIONAL_ERROR_MARKERS)


async def evaluate_symbol_contracts(
    provider: Any,
    ticker: str,
    *,
    now: datetime,
    underlying_price: float | None = None,
    preferred_max_contract_cost_dollars: float = PREFERRED_MAX_CONTRACT_COST_DOLLARS,
    monotonic: Callable[[], float] = time.monotonic,
) -> SymbolContractResult:
    """Fetch exactly one V1-selected expiry/chain and classify CALL + PUT."""
    symbol = str(ticker).strip().upper()
    started = monotonic()

    expirations = await provider.fetch_option_expirations(symbol)
    expiry_decision = choose_expiration(expirations, now)
    if not expiry_decision.valid or expiry_decision.expiry is None:
        error = str(getattr(provider, "last_error", "") or "") or expiry_decision.reason
        elapsed = max(0.0, monotonic() - started)
        reason = expiry_decision.reason or error or "no_expiration_in_v1_dte_range"
        return SymbolContractResult(
            ticker=symbol,
            elapsed_seconds=round(elapsed, 6),
            expiration=None,
            dte=None,
            dte_bucket=None,
            expiration_warning=None,
            chain_error=error or None,
            operational_error=_operational_error(error),
            call=_empty_side("CALL", reason),
            put=_empty_side("PUT", reason),
        )

    expiry = expiry_decision.expiry
    chain = await provider.fetch_option_chain(symbol, expiry.expiration)
    chain_error = str(getattr(chain, "error", "") or getattr(provider, "last_error", "") or "")
    if chain_error:
        elapsed = max(0.0, monotonic() - started)
        return SymbolContractResult(
            ticker=symbol,
            elapsed_seconds=round(elapsed, 6),
            expiration=expiry.expiration,
            dte=expiry.dte,
            dte_bucket=expiry.bucket,
            expiration_warning=expiry.warning or None,
            chain_error=chain_error,
            operational_error=_operational_error(chain_error),
            call=_empty_side("CALL", f"chain_error:{chain_error}"),
            put=_empty_side("PUT", f"chain_error:{chain_error}"),
        )

    # The exact V1 scanner ranks strikes using spot.  If the caller did not
    # already have a quote, one read-only underlying snapshot supplies it.
    spot = underlying_price
    if spot is None:
        snapshot = await provider.fetch_market_snapshot(symbol)
        spot = getattr(snapshot, "price", None)
        snapshot_error = str(getattr(snapshot, "error", "") or "")
        if snapshot_error and _operational_error(snapshot_error):
            elapsed = max(0.0, monotonic() - started)
            return SymbolContractResult(
                ticker=symbol,
                elapsed_seconds=round(elapsed, 6),
                expiration=expiry.expiration,
                dte=expiry.dte,
                dte_bucket=expiry.bucket,
                expiration_warning=expiry.warning or None,
                chain_error=snapshot_error,
                operational_error=True,
                call=_empty_side("CALL", f"underlying_snapshot_error:{snapshot_error}"),
                put=_empty_side("PUT", f"underlying_snapshot_error:{snapshot_error}"),
            )

    call = _side_result(
        chain,
        side="CALL",
        underlying_price=spot,
        preferred_max_contract_cost_dollars=preferred_max_contract_cost_dollars,
    )
    put = _side_result(
        chain,
        side="PUT",
        underlying_price=spot,
        preferred_max_contract_cost_dollars=preferred_max_contract_cost_dollars,
    )
    return SymbolContractResult(
        ticker=symbol,
        elapsed_seconds=round(max(0.0, monotonic() - started), 6),
        expiration=expiry.expiration,
        dte=expiry.dte,
        dte_bucket=expiry.bucket,
        expiration_warning=expiry.warning or None,
        chain_error=None,
        operational_error=False,
        call=call,
        put=put,
    )


async def run_contract_census(
    provider: Any,
    tickers: Sequence[str],
    *,
    now: datetime,
    preferred_max_contract_cost_dollars: float = PREFERRED_MAX_CONTRACT_COST_DOLLARS,
    monotonic: Callable[[], float] = time.monotonic,
) -> ContractCensusReport:
    """Run a serial, read-only contract census. Provider failures fail closed."""
    requested = [str(t).strip().upper() for t in tickers if str(t).strip()]
    overall_started = monotonic()
    results: list[SymbolContractResult] = []
    for ticker in requested:
        try:
            result = await evaluate_symbol_contracts(
                provider,
                ticker,
                now=now,
                preferred_max_contract_cost_dollars=preferred_max_contract_cost_dollars,
                monotonic=monotonic,
            )
        except BaseException as exc:
            result = SymbolContractResult(
                ticker=ticker,
                elapsed_seconds=0.0,
                expiration=None,
                dte=None,
                dte_bucket=None,
                expiration_warning=None,
                chain_error=f"exception:{type(exc).__name__}:{exc}",
                operational_error=True,
                call=_empty_side("CALL", "provider_exception"),
                put=_empty_side("PUT", "provider_exception"),
            )
        results.append(result)

    operational = [item for item in results if item.operational_error]
    complete = len(results) == len(requested) and bool(requested)
    reasons: list[str] = []
    if not complete:
        reasons.append("incomplete_census")
    if operational:
        reasons.append(f"operational_failures:{len(operational)}")

    both = tuple(item.ticker for item in results if item.viable_sides == 2)
    any_side = tuple(item.ticker for item in results if item.viable_sides >= 1)
    preferred_both = tuple(item.ticker for item in results if item.preferred_cost_viable_sides == 2)
    preferred_any = tuple(item.ticker for item in results if item.preferred_cost_viable_sides >= 1)
    none = tuple(item.ticker for item in results if item.viable_sides == 0)
    return ContractCensusReport(
        verdict="PASS" if not reasons else "FAIL",
        reasons=tuple(reasons),
        candidate_count=len(requested),
        tested_count=len(results),
        total_elapsed_seconds=round(max(0.0, monotonic() - overall_started), 6),
        operational_failures=len(operational),
        symbols_v1_both_sides=both,
        symbols_v1_any_side=any_side,
        symbols_preferred_cost_both_sides=preferred_both,
        symbols_preferred_cost_any_side=preferred_any,
        symbols_no_v1_contract=none,
        contract_fetches=len(results),
        storage_writes=0,
        alerts_sent=0,
        live_watchlist_changed=False,
        results=tuple(results),
    )
