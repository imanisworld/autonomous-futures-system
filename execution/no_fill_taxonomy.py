"""Coarse cause-of-no-fill classification.

Logging/reporting only — nothing here changes order, risk, or broker
behavior. It buckets the broker-native reason strings that already exist in
Fill.exit_reason (e.g. "ENTRY_NOT_FILLED", "TRADOVATE_REJECTED") into a small
taxonomy so cancelled/no-fill outcomes are diagnosable without re-reading
free-text reasons by hand.

The taxonomy deliberately does not claim more precision than the underlying
data supports. Where the live/paper brokers cannot distinguish two causes
(e.g. no captured bid/ask at cancel time), this maps to NO_FILL_UNKNOWN
rather than guessing.
"""

from __future__ import annotations

from typing import Optional

NO_FILL_PRICE_MOVED_AWAY = "NO_FILL_PRICE_MOVED_AWAY"
NO_FILL_LIMIT_TOO_PASSIVE = "NO_FILL_LIMIT_TOO_PASSIVE"
NO_FILL_TIMEOUT_TOO_SHORT = "NO_FILL_TIMEOUT_TOO_SHORT"
NO_FILL_SIGNAL_LATE = "NO_FILL_SIGNAL_LATE"
NO_FILL_BROKER_REJECTED = "NO_FILL_BROKER_REJECTED"
NO_FILL_SESSION_OR_RISK_CANCEL = "NO_FILL_SESSION_OR_RISK_CANCEL"
NO_FILL_DUPLICATE_CANCEL = "NO_FILL_DUPLICATE_CANCEL"
NO_FILL_UNKNOWN = "NO_FILL_UNKNOWN"

# Execution-provider failure buckets. Tradovate reports these in the
# rejection reason of a placeOSO/placeOrder response (failureReason/
# rejectReason/text). Each is fail-closed — the order is NEVER re-routed to
# an alternate order type; classification is for the journal/diagnostics.
NO_FILL_NO_QUOTE = "NO_FILL_NO_QUOTE"
NO_FILL_NO_LIQUIDITY = "NO_FILL_NO_LIQUIDITY"
NO_FILL_PROVIDER_UNAVAILABLE = "NO_FILL_PROVIDER_UNAVAILABLE"
NO_FILL_RISK_CHECK_TIMEOUT = "NO_FILL_RISK_CHECK_TIMEOUT"
NO_FILL_SESSION_CLOSED = "NO_FILL_SESSION_CLOSED"
NO_FILL_LIQUIDATION_ONLY = "NO_FILL_LIQUIDATION_ONLY"
NO_FILL_MAX_POSITION = "NO_FILL_MAX_POSITION"
NO_FILL_MAX_ORDER_QTY = "NO_FILL_MAX_ORDER_QTY"

ALL_REASONS = (
    NO_FILL_PRICE_MOVED_AWAY,
    NO_FILL_LIMIT_TOO_PASSIVE,
    NO_FILL_TIMEOUT_TOO_SHORT,
    NO_FILL_SIGNAL_LATE,
    NO_FILL_BROKER_REJECTED,
    NO_FILL_SESSION_OR_RISK_CANCEL,
    NO_FILL_DUPLICATE_CANCEL,
    NO_FILL_UNKNOWN,
    NO_FILL_NO_QUOTE,
    NO_FILL_NO_LIQUIDITY,
    NO_FILL_PROVIDER_UNAVAILABLE,
    NO_FILL_RISK_CHECK_TIMEOUT,
    NO_FILL_SESSION_CLOSED,
    NO_FILL_LIQUIDATION_ONLY,
    NO_FILL_MAX_POSITION,
    NO_FILL_MAX_ORDER_QTY,
)

# Substring → bucket for provider failure strings (matched case-insensitively
# against the union of Tradovate's rejection-reason fields). Order matters:
# first match wins; more specific substrings come first.
_PROVIDER_FAILURE_MAP = (
    ("noquote", NO_FILL_NO_QUOTE),
    ("no quote", NO_FILL_NO_QUOTE),
    ("notenoughliquidity", NO_FILL_NO_LIQUIDITY),
    ("not enough liquidity", NO_FILL_NO_LIQUIDITY),
    ("executionproviderunavailable", NO_FILL_PROVIDER_UNAVAILABLE),
    ("execution provider unavailable", NO_FILL_PROVIDER_UNAVAILABLE),
    ("riskchecktimeout", NO_FILL_RISK_CHECK_TIMEOUT),
    ("risk check timeout", NO_FILL_RISK_CHECK_TIMEOUT),
    ("sessionclosed", NO_FILL_SESSION_CLOSED),
    ("session closed", NO_FILL_SESSION_CLOSED),
    ("liquidationonly", NO_FILL_LIQUIDATION_ONLY),
    ("liquidation only", NO_FILL_LIQUIDATION_ONLY),
    ("backmonthprohibited", NO_FILL_LIQUIDATION_ONLY),
    ("back month prohibited", NO_FILL_LIQUIDATION_ONLY),
    ("maxpositionlimit", NO_FILL_MAX_POSITION),
    ("max position", NO_FILL_MAX_POSITION),
    ("maxorderqty", NO_FILL_MAX_ORDER_QTY),
    ("max order qty", NO_FILL_MAX_ORDER_QTY),
    ("maxorderquantity", NO_FILL_MAX_ORDER_QTY),
)


def classify_provider_failure(reject_text: Optional[str]) -> Optional[str]:
    """Bucket a Tradovate rejection-reason string into an explicit provider
    failure, or None when it doesn't match a known provider-failure pattern
    (callers then fall back to the coarse NO_FILL_BROKER_REJECTED)."""
    if not reject_text:
        return None
    text = str(reject_text).strip().lower()
    for needle, bucket in _PROVIDER_FAILURE_MAP:
        if needle in text:
            return bucket
    return None

# Broker-native reason -> coarse bucket, for reasons that map unambiguously
# regardless of finer context. "ENTRY_NOT_FILLED" is handled separately below
# because its correct bucket depends on entry_status ("dead" vs "working").
_BROKER_REASON_MAP = {
    "LIVE_TRADING_NOT_ENABLED": NO_FILL_SESSION_OR_RISK_CANCEL,
    "LIVE_PREFLIGHT_NOT_ARMED": NO_FILL_SESSION_OR_RISK_CANCEL,
    "BROKER_NOT_READY": NO_FILL_BROKER_REJECTED,
    "TRADOVATE_AUTH_FAILED": NO_FILL_BROKER_REJECTED,
    "TRADOVATE_REJECTED": NO_FILL_BROKER_REJECTED,
    "TRADOVATE_NO_ORDER_ID": NO_FILL_BROKER_REJECTED,
    "ENTRY_UNCONFIRMED": NO_FILL_UNKNOWN,
    # PaperBroker's stop_market entry model: the stop-market never triggered
    # because price never traded through the entry level before expiry.
    "ENTRY_NOT_TRIGGERED": NO_FILL_PRICE_MOVED_AWAY,
    # Execution-mode safety refusals (fail-closed before any submission).
    "EXECUTION_MODE_NOT_ALLOWED_LIVE": NO_FILL_SESSION_OR_RISK_CANCEL,
    "EXECUTION_MODE_INVALID": NO_FILL_SESSION_OR_RISK_CANCEL,
    "EXECUTION_MODE_MISCONFIGURED": NO_FILL_SESSION_OR_RISK_CANCEL,
    # Client-order-identity idempotency refusals: the same logical signal must
    # never create a second parent order (retry after an ambiguous submission
    # requires reconciliation first, never a blind re-fire).
    "DUPLICATE_CLIENT_ORDER_ID": NO_FILL_DUPLICATE_CANCEL,
    "SUBMIT_AMBIGUOUS_UNRECONCILED": NO_FILL_DUPLICATE_CANCEL,
    # Account-routing guard refusals (fail-closed before any submission) — see
    # TradovateBroker._verify_account_for_order.
    "ACCOUNT_UNRESOLVED": NO_FILL_SESSION_OR_RISK_CANCEL,
    "ACCOUNT_MISMATCH": NO_FILL_SESSION_OR_RISK_CANCEL,
    "ACCOUNT_ZERO_BUYING_POWER": NO_FILL_SESSION_OR_RISK_CANCEL,
}


def classify_no_fill_reason(
    broker_reason: Optional[str],
    *,
    entry_status: Optional[str] = None,
) -> str:
    """Bucket a broker-native no-fill reason into the coarse taxonomy.

    entry_status disambiguates "ENTRY_NOT_FILLED", the one reason that covers
    two different situations at the Tradovate IOC-limit entry leg:
      - "dead":    the exchange rejected/self-cancelled the IOC the instant it
                   was submitted — the market was already through the limit.
      - "working": the order rested on the book unfilled until this system's
                   own poll cancelled it — the limit price never traded.
    Anything unrecognized (including entry_status=None for ENTRY_NOT_FILLED)
    returns NO_FILL_UNKNOWN rather than guessing.
    """
    if not broker_reason:
        return NO_FILL_UNKNOWN
    reason = str(broker_reason).strip().upper()
    if reason == "ENTRY_NOT_FILLED":
        if entry_status == "dead":
            return NO_FILL_PRICE_MOVED_AWAY
        if entry_status == "working":
            return NO_FILL_LIMIT_TOO_PASSIVE
        return NO_FILL_UNKNOWN
    return _BROKER_REASON_MAP.get(reason, NO_FILL_UNKNOWN)
