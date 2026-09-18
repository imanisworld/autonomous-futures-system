"""Deterministic adapter for replaying frozen retained option quotes.

This module is deliberately runtime-agnostic: replay and forward-proof fixtures
must consume the same serialized QuoteRecord bytes rather than reconstructing
quote state through separate code paths.
"""

from __future__ import annotations

from dataclasses import fields
import json
from typing import Any

from .retention import QuoteRecord

_ALLOWED_STATUSES = {"OK", "MISSING", "STALE", "FUTURE", "INVALID", "WIDE_SPREAD"}
_RECORD_FIELDS = {field.name for field in fields(QuoteRecord)}


def quote_record_from_json_line(payload: bytes) -> QuoteRecord:
    """Parse one frozen JSONL record, failing closed on byte/schema drift."""
    if not isinstance(payload, bytes) or not payload or b"\n" in payload.rstrip(b"\n"):
        raise ValueError("retained quote payload must contain exactly one JSONL record")
    try:
        row: Any = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("retained quote payload is not valid JSON") from exc
    if not isinstance(row, dict):
        raise ValueError("retained quote record must be an object")
    missing = sorted(_RECORD_FIELDS - set(row))
    extra = sorted(set(row) - _RECORD_FIELDS)
    if missing or extra:
        raise ValueError(f"retained quote schema mismatch: missing={missing}, extra={extra}")
    if row.get("status") not in _ALLOWED_STATUSES:
        raise ValueError(f"retained quote status invalid: {row.get('status')!r}")
    missing_fields = row.get("missing_fields")
    if not isinstance(missing_fields, list) or not all(isinstance(item, str) for item in missing_fields):
        raise ValueError("retained quote missing_fields must be a string list")
    row["missing_fields"] = tuple(missing_fields)
    return QuoteRecord(**row)


def executable_quote_projection(payload: bytes) -> dict[str, object]:
    """Return the shared replay/forward selector-input projection.

    Identity/liquidity fields are preserved for the selector, but executable
    bid/ask are exposed only for an OK retained record. This keeps replay from
    rebuilding a cleaner quote than the forward evidence actually retained.
    """
    record = quote_record_from_json_line(payload)
    executable = record.status == "OK"
    return {
        "contract_id": record.contract_id,
        "symbol": record.contract_id,
        "underlying": record.underlying,
        "expiration": record.expiration,
        "strike": record.strike,
        "right": record.right,
        "option_type": record.right,
        "decision_ts": record.decision_ts,
        "quote_ts": record.quote_ts,
        "quote_timestamp": record.quote_ts,
        "source": record.source,
        "status": record.status,
        "reason_code": record.reason_code,
        "bid": record.bid if executable else None,
        "ask": record.ask if executable else None,
        "mid": ((record.bid + record.ask) / 2.0) if executable and record.bid is not None and record.ask is not None else None,
        "volume": record.volume,
        "open_interest": record.open_interest,
        "delta": record.delta,
        "implied_volatility": record.iv,
        "executable": executable,
    }
