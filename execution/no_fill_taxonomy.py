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

ALL_REASONS = (
    NO_FILL_PRICE_MOVED_AWAY,
    NO_FILL_LIMIT_TOO_PASSIVE,
    NO_FILL_TIMEOUT_TOO_SHORT,
    NO_FILL_SIGNAL_LATE,
    NO_FILL_BROKER_REJECTED,
    NO_FILL_SESSION_OR_RISK_CANCEL,
    NO_FILL_DUPLICATE_CANCEL,
    NO_FILL_UNKNOWN,
)

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
