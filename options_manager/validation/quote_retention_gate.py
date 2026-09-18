"""Pure fail-closed bridge from canonical advisory intake to quote retention."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping, Optional

from options_manager.quotes import (
    QuoteRecord,
    QuoteRetentionInput,
    QuoteRetentionRule,
    retain_quote,
)
from .contract_quality_gate import ContractQualityInput


@dataclass(frozen=True, kw_only=True)
class QuoteRetentionGateResult:
    approved: bool
    blocking_reasons: tuple[str, ...] = ()
    record: Optional[QuoteRecord] = None


def _same_number(left: object, right: object, *, abs_tol: float = 1e-9) -> bool:
    try:
        a = float(left)
        b = float(right)
    except (TypeError, ValueError):
        return False
    return math.isfinite(a) and math.isfinite(b) and math.isclose(
        a, b, rel_tol=0.0, abs_tol=abs_tol
    )


def check_quote_retention_intake(
    payload: Any,
    *,
    contract: Optional[ContractQualityInput],
    rule: QuoteRetentionRule,
    rule_sha256: str,
) -> QuoteRetentionGateResult:
    """Normalize one canonical quote payload, retain it, and cross-check contract identity."""

    if not isinstance(payload, Mapping):
        return QuoteRetentionGateResult(
            approved=False,
            blocking_reasons=(
                f"malformed quote_retention payload: expected mapping, got {type(payload).__name__}",
            ),
        )

    quote = QuoteRetentionInput(
        contract_id=payload.get("contract_id"),
        underlying=payload.get("underlying"),
        expiration=payload.get("expiration"),
        strike=payload.get("strike"),
        right=payload.get("right"),
        bid=payload.get("bid"),
        ask=payload.get("ask"),
        quote_ts=payload.get("quote_ts"),
        decision_ts=payload.get("decision_ts"),
        source=payload.get("source"),
        volume=payload.get("volume"),
        open_interest=payload.get("open_interest"),
        delta=payload.get("delta"),
        iv=payload.get("iv"),
    )

    try:
        record = retain_quote(quote, rule=rule, rule_sha256=rule_sha256)
    except (TypeError, ValueError) as exc:
        return QuoteRetentionGateResult(
            approved=False,
            blocking_reasons=(f"quote retention rule/input invalid: {exc}",),
        )

    blocking: list[str] = []
    if record.status != "OK":
        blocking.append(f"quote status {record.status}: {record.reason_code}")

    if contract is None:
        blocking.append("contract quality record unavailable for quote reconciliation")
    else:
        comparisons = (
            ("underlying/ticker", record.underlying, contract.ticker, False),
            ("expiration", record.expiration, contract.expiration, False),
            ("right/direction", record.right, contract.direction, False),
            ("strike", record.strike, contract.strike, True),
            ("bid", record.bid, contract.bid, True),
            ("ask", record.ask, contract.ask, True),
            ("spread_percent", record.spread_percent, contract.spread_percent, True),
            ("volume", record.volume, contract.volume, True),
            ("open_interest", record.open_interest, contract.open_interest, True),
        )
        for label, observed, expected, numeric in comparisons:
            if numeric:
                # Contract-quality payloads historically carry spread_percent
                # rounded to one decimal place, while retention recomputes it
                # directly from bid/ask. Permit only that representation
                # tolerance; all other numeric identity fields remain exact.
                abs_tol = 0.05 if label == "spread_percent" else 1e-9
                matches = _same_number(observed, expected, abs_tol=abs_tol)
            else:
                matches = observed == expected
            if not matches:
                blocking.append(
                    f"quote/contract mismatch for {label}: quote={observed!r}, contract={expected!r}"
                )

    return QuoteRetentionGateResult(
        approved=not blocking,
        blocking_reasons=tuple(blocking),
        record=record,
    )
