"""Read-only capacity measurement for a broader V1 options universe.

This module measures only the data-building stage that precedes V1 scoring and
contract selection.  It intentionally does not call ``scan_ticker`` or
``scan_watchlist``: those methods write scanner/shadow evidence and may fetch an
option chain for a triggered setup.  The capacity probe calls the scanner's
normalized-data builder directly, in the same *serial ticker order* used by the
live scanner, so provider latency and causal-bar availability can be measured
without touching V1 state.

A PASS here is necessary, not sufficient, for activation.  It proves that the
current read-only snapshot/context/observational-enrichment path can traverse
all candidate symbols inside one configured scanner interval with no critical
data failures.  It does not prove option-chain capacity because contract
fetches are deliberately forbidden in this lane.
"""
from __future__ import annotations

import math
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Callable, Sequence


RATE_LIMIT_MARKERS = ("rate_limited", "rate limited", "http 429", "status_429")
TIMEOUT_MARKERS = ("timeout", "readtimeout", "connecttimeout", "timed out")
MISSING_BAR_MARKERS = (
    "missing_bar",
    "missing_symbol",
    "incomplete_session",
    "no_bars",
    "bar_gap",
)


@dataclass(frozen=True)
class SymbolCapacityResult:
    ticker: str
    elapsed_seconds: float
    price_present: bool
    market_data_error: str | None
    bar_context_available: bool
    bar_context_reason: str | None
    missing_bar_count: int
    signa_error: str | None
    signa_v2_ok: bool | None
    signa_v2_error: str | None
    setup_status: str | None
    rate_limited: bool
    timed_out: bool
    missing_bars: bool
    critical_ok: bool
    exception: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CapacityReport:
    verdict: str
    reasons: tuple[str, ...]
    started_at: str
    requested_as_of: str
    candidate_count: int
    tested_count: int
    configured_interval_seconds: float
    total_elapsed_seconds: float
    interval_utilization: float | None
    headroom_seconds: float
    p50_symbol_seconds: float | None
    p95_symbol_seconds: float | None
    max_symbol_seconds: float | None
    critical_failures: int
    observational_failures: int
    rate_limited_symbols: tuple[str, ...]
    timed_out_symbols: tuple[str, ...]
    missing_bar_symbols: tuple[str, ...]
    market_data_error_symbols: tuple[str, ...]
    bar_context_error_symbols: tuple[str, ...]
    signa_error_symbols: tuple[str, ...]
    complete_universe: bool
    serial_order_preserved: bool
    contract_fetches: int
    storage_writes: int
    alerts_sent: int
    live_watchlist_changed: bool
    results: tuple[SymbolCapacityResult, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["results"] = [item.to_dict() for item in self.results]
        return payload


def _text(value: Any) -> str:
    return str(value or "").strip()


def _contains_any(values: Sequence[Any], markers: Sequence[str]) -> bool:
    haystack = " | ".join(_text(value).lower() for value in values if value not in (None, ""))
    return any(marker in haystack for marker in markers)


def _int_or_zero(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def classify_symbol_result(ticker: str, elapsed_seconds: float, raw: dict[str, Any] | None, *, exception: BaseException | None = None) -> SymbolCapacityResult:
    """Normalize one read-only data-build result into capacity telemetry."""
    data = raw if isinstance(raw, dict) else {}
    market_error = _text(data.get("market_data_error")) or None
    bar_available = bool(data.get("bar_context_available"))
    bar_reason = _text(data.get("bar_context_reason")) or None
    missing_bar_count = _int_or_zero(data.get("missing_bar_count"))
    signa_error = _text(data.get("signa_error")) or None
    signa_v2_ok = data.get("signa_v2_ok")
    if signa_v2_ok is not None:
        signa_v2_ok = bool(signa_v2_ok)
    signa_v2_error = _text(data.get("signa_v2_error")) or None
    exception_text = f"{type(exception).__name__}:{exception}" if exception is not None else None

    error_values = (market_error, bar_reason, signa_error, signa_v2_error, exception_text)
    rate_limited = _contains_any(error_values, RATE_LIMIT_MARKERS)
    timed_out = _contains_any(error_values, TIMEOUT_MARKERS)
    missing_bars = missing_bar_count > 0 or _contains_any((bar_reason,), MISSING_BAR_MARKERS)

    # Signa is observational in V1 and does not make price-action evidence
    # invalid.  Snapshot and causal-bar failures are critical; Signa failures
    # remain visible and still consume measured cycle time.
    price_present = data.get("price") not in (None, "")
    critical_ok = (
        exception is None
        and price_present
        and market_error is None
        and bar_available
        and not missing_bars
    )
    return SymbolCapacityResult(
        ticker=ticker,
        elapsed_seconds=round(max(0.0, float(elapsed_seconds)), 6),
        price_present=price_present,
        market_data_error=market_error,
        bar_context_available=bar_available,
        bar_context_reason=bar_reason,
        missing_bar_count=missing_bar_count,
        signa_error=signa_error,
        signa_v2_ok=signa_v2_ok,
        signa_v2_error=signa_v2_error,
        setup_status=_text(data.get("setup_status")) or None,
        rate_limited=rate_limited,
        timed_out=timed_out,
        missing_bars=missing_bars,
        critical_ok=critical_ok,
        exception=exception_text,
    )


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return round(ordered[0], 6)
    index = (len(ordered) - 1) * fraction
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return round(ordered[lower], 6)
    weight = index - lower
    return round(ordered[lower] * (1.0 - weight) + ordered[upper] * weight, 6)


async def run_serial_capacity_preflight(
    scanner: Any,
    tickers: Sequence[str],
    *,
    now: datetime,
    interval_seconds: float,
    monotonic: Callable[[], float] = time.monotonic,
) -> CapacityReport:
    """Measure the scanner's read-only normalized-data stage, one ticker at a time.

    ``scanner`` must expose ``_build_normalized_data(ticker, context, now)``.
    The function intentionally continues after an individual ticker exception
    so the report can identify every failing symbol; exceptions are still
    critical failures and therefore force the overall verdict to FAIL.
    """
    requested = [str(ticker).strip().upper() for ticker in tickers if str(ticker).strip()]
    started_wall = datetime.now(now.tzinfo) if now.tzinfo is not None else datetime.now()
    overall_start = monotonic()
    results: list[SymbolCapacityResult] = []
    observed_order: list[str] = []

    for ticker in requested:
        observed_order.append(ticker)
        one_start = monotonic()
        raw: dict[str, Any] | None = None
        caught: BaseException | None = None
        try:
            raw = await scanner._build_normalized_data(ticker, {}, now)
        except BaseException as exc:  # fail closed in report, but finish the census
            caught = exc
        elapsed = monotonic() - one_start
        results.append(classify_symbol_result(ticker, elapsed, raw, exception=caught))

    total_elapsed = max(0.0, monotonic() - overall_start)
    budget = max(0.0, float(interval_seconds))
    critical = [item for item in results if not item.critical_ok]
    observational = [
        item
        for item in results
        if item.signa_error is not None or item.signa_v2_error is not None or item.signa_v2_ok is False
    ]
    rate_limited = tuple(item.ticker for item in results if item.rate_limited)
    timed_out = tuple(item.ticker for item in results if item.timed_out)
    missing_bars = tuple(item.ticker for item in results if item.missing_bars)
    market_errors = tuple(item.ticker for item in results if item.market_data_error is not None)
    bar_errors = tuple(item.ticker for item in results if not item.bar_context_available)
    signa_errors = tuple(
        item.ticker
        for item in results
        if item.signa_error is not None or item.signa_v2_error is not None or item.signa_v2_ok is False
    )
    complete = len(results) == len(requested) and len(requested) > 0
    serial_order = observed_order == requested

    reasons: list[str] = []
    if not complete:
        reasons.append("incomplete_universe")
    if not serial_order:
        reasons.append("serial_order_changed")
    if critical:
        reasons.append(f"critical_data_failures:{len(critical)}")
    if rate_limited:
        reasons.append(f"rate_limited:{len(rate_limited)}")
    if timed_out:
        reasons.append(f"timeouts:{len(timed_out)}")
    if budget <= 0:
        reasons.append("invalid_interval_budget")
    elif total_elapsed > budget:
        reasons.append("cycle_exceeds_configured_interval")

    durations = [item.elapsed_seconds for item in results]
    utilization = round(total_elapsed / budget, 6) if budget > 0 else None
    return CapacityReport(
        verdict="PASS" if not reasons else "FAIL",
        reasons=tuple(reasons),
        started_at=started_wall.isoformat(),
        requested_as_of=now.isoformat(),
        candidate_count=len(requested),
        tested_count=len(results),
        configured_interval_seconds=round(budget, 6),
        total_elapsed_seconds=round(total_elapsed, 6),
        interval_utilization=utilization,
        headroom_seconds=round(budget - total_elapsed, 6),
        p50_symbol_seconds=round(statistics.median(durations), 6) if durations else None,
        p95_symbol_seconds=_percentile(durations, 0.95),
        max_symbol_seconds=round(max(durations), 6) if durations else None,
        critical_failures=len(critical),
        observational_failures=len(observational),
        rate_limited_symbols=rate_limited,
        timed_out_symbols=timed_out,
        missing_bar_symbols=missing_bars,
        market_data_error_symbols=market_errors,
        bar_context_error_symbols=bar_errors,
        signa_error_symbols=signa_errors,
        complete_universe=complete,
        serial_order_preserved=serial_order,
        contract_fetches=0,
        storage_writes=0,
        alerts_sent=0,
        live_watchlist_changed=False,
        results=tuple(results),
    )
