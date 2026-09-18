"""Deterministic option quote-retention primitives.

This module does not fetch quotes, call a broker, read environment variables, or
activate any runtime path. It receives caller-supplied decision-time quote data,
classifies it fail-closed, and emits canonical serializable records suitable for
later replay/evidence use.

Only status=OK records are eligible for later fill reconstruction. MISSING,
STALE, FUTURE, INVALID, and WIDE_SPREAD remain retained evidence but must be
rejected by consumers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
import hashlib
import json
import math
from typing import Literal, Mapping, Optional

QuoteStatus = Literal["OK", "MISSING", "STALE", "FUTURE", "INVALID", "WIDE_SPREAD"]


class QuoteSource(str, Enum):
    """Frozen source routes already represented by this repository.

    These are identifiers, not network clients. Adding a new real provider
    requires an explicit rule/version change rather than accepting free text.
    """

    # Test-only source. Real provider sources must be added only when the
    # provider+endpoint identity is mechanically known at ingestion time.
    FIXTURE_OPTION_CHAIN = "fixture:option_chain_snapshot"


@dataclass(frozen=True)
class QuoteRetentionRule:
    rule_id: str
    max_quote_age_seconds: int
    max_spread_percent: float
    allowed_sources: tuple[QuoteSource, ...]


@dataclass(frozen=True)
class QuoteRetentionInput:
    contract_id: Optional[str]
    underlying: Optional[str]
    expiration: Optional[str]
    strike: Optional[float]
    right: Optional[str]
    bid: Optional[float]
    ask: Optional[float]
    quote_ts: Optional[str]
    decision_ts: Optional[str]
    source: Optional[str]
    volume: Optional[int]
    open_interest: Optional[int]
    delta: Optional[float]
    iv: Optional[float]


@dataclass(frozen=True)
class QuoteRecord:
    status: QuoteStatus
    reason_code: str
    rule_id: str
    rule_sha256: str
    contract_id: Optional[str]
    underlying: Optional[str]
    expiration: Optional[str]
    strike: Optional[float]
    right: Optional[str]
    bid: Optional[float]
    ask: Optional[float]
    quote_ts: Optional[str]
    decision_ts: Optional[str]
    source: Optional[str]
    volume: Optional[int]
    open_interest: Optional[int]
    delta: Optional[float]
    iv: Optional[float]
    spread_percent: Optional[float]
    quote_age_seconds: Optional[float]
    missing_fields: tuple[str, ...] = field(default_factory=tuple)


def retention_rule_from_mapping(raw: Mapping[str, object]) -> QuoteRetentionRule:
    required = (
        "rule_id",
        "max_quote_age_seconds",
        "max_spread_percent",
        "allowed_sources",
    )
    missing = [key for key in required if key not in raw]
    if missing:
        raise ValueError(f"missing retention rule fields: {','.join(sorted(missing))}")

    rule_id = raw["rule_id"]
    if not isinstance(rule_id, str) or not rule_id.strip():
        raise ValueError("rule_id must be a non-empty string")

    age = raw["max_quote_age_seconds"]
    if isinstance(age, bool) or not isinstance(age, int) or age <= 0:
        raise ValueError("max_quote_age_seconds must be a positive integer")

    spread = raw["max_spread_percent"]
    if isinstance(spread, bool) or not isinstance(spread, (int, float)):
        raise ValueError("max_spread_percent must be numeric")
    spread = float(spread)
    if not math.isfinite(spread) or spread <= 0:
        raise ValueError("max_spread_percent must be finite and > 0")

    sources = raw["allowed_sources"]
    if not isinstance(sources, list) or not sources:
        raise ValueError("allowed_sources must be a non-empty list")
    parsed_sources: list[QuoteSource] = []
    for source in sources:
        if not isinstance(source, str):
            raise ValueError("allowed_sources entries must be strings")
        try:
            parsed_sources.append(QuoteSource(source))
        except ValueError as exc:
            raise ValueError(f"unsupported quote source: {source!r}") from exc
    if len(set(parsed_sources)) != len(parsed_sources):
        raise ValueError("allowed_sources must not contain duplicates")

    return QuoteRetentionRule(
        rule_id=rule_id.strip(),
        max_quote_age_seconds=age,
        max_spread_percent=spread,
        allowed_sources=tuple(parsed_sources),
    )


def quote_record_json(record: QuoteRecord) -> str:
    """Canonical byte-stable JSONL representation."""

    return json.dumps(asdict(record), sort_keys=True, separators=(",", ":")) + "\n"


def _parse_ts(value: Optional[str]) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed


def _finite(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in "0123456789abcdefABCDEF" for ch in value)
    )


def _spread_percent(bid: float, ask: float) -> float:
    midpoint = (bid + ask) / 2.0
    return ((ask - bid) / midpoint) * 100.0


def retain_quote(
    quote: QuoteRetentionInput,
    *,
    rule: QuoteRetentionRule,
    rule_sha256: str,
) -> QuoteRecord:
    """Classify and retain one decision-time option quote without fabrication."""

    if not _valid_sha256(rule_sha256):
        raise ValueError("rule_sha256 must be a 64-character hexadecimal SHA-256")
    rule_sha256 = rule_sha256.lower()

    required_values = {
        "contract_id": quote.contract_id,
        "underlying": quote.underlying,
        "expiration": quote.expiration,
        "strike": quote.strike,
        "right": quote.right,
        "bid": quote.bid,
        "ask": quote.ask,
        "quote_ts": quote.quote_ts,
        "decision_ts": quote.decision_ts,
        "source": quote.source,
        "volume": quote.volume,
        "open_interest": quote.open_interest,
        "delta": quote.delta,
        "iv": quote.iv,
    }
    missing_fields = tuple(
        sorted(
            key
            for key, value in required_values.items()
            if value is None or (isinstance(value, str) and not value.strip())
        )
    )

    base = dict(
        rule_id=rule.rule_id,
        rule_sha256=rule_sha256,
        contract_id=quote.contract_id,
        underlying=quote.underlying,
        expiration=quote.expiration,
        strike=quote.strike,
        right=quote.right,
        bid=quote.bid,
        ask=quote.ask,
        quote_ts=quote.quote_ts,
        decision_ts=quote.decision_ts,
        source=quote.source,
        volume=quote.volume,
        open_interest=quote.open_interest,
        delta=quote.delta,
        iv=quote.iv,
        spread_percent=None,
        quote_age_seconds=None,
        missing_fields=missing_fields,
    )

    if missing_fields:
        return QuoteRecord(status="MISSING", reason_code="required_field_missing", **base)

    try:
        source = QuoteSource(str(quote.source))
    except ValueError:
        return QuoteRecord(status="INVALID", reason_code="source_not_allowed", **base)
    if source not in rule.allowed_sources:
        return QuoteRecord(status="INVALID", reason_code="source_not_allowed", **base)

    if quote.right not in ("CALL", "PUT"):
        return QuoteRecord(status="INVALID", reason_code="right_invalid", **base)

    numerics = {
        "strike": quote.strike,
        "bid": quote.bid,
        "ask": quote.ask,
        "delta": quote.delta,
        "iv": quote.iv,
    }
    if not all(_finite(value) for value in numerics.values()):
        return QuoteRecord(status="INVALID", reason_code="numeric_field_invalid", **base)

    if (
        isinstance(quote.volume, bool)
        or not isinstance(quote.volume, int)
        or quote.volume < 0
        or isinstance(quote.open_interest, bool)
        or not isinstance(quote.open_interest, int)
        or quote.open_interest < 0
    ):
        return QuoteRecord(status="INVALID", reason_code="liquidity_field_invalid", **base)

    strike = float(quote.strike)
    bid = float(quote.bid)
    ask = float(quote.ask)
    iv = float(quote.iv)
    if strike <= 0 or bid <= 0 or ask < bid or iv <= 0:
        return QuoteRecord(status="INVALID", reason_code="quote_values_invalid", **base)

    try:
        quote_dt = _parse_ts(quote.quote_ts)
        decision_dt = _parse_ts(quote.decision_ts)
    except ValueError:
        return QuoteRecord(status="INVALID", reason_code="timestamp_invalid", **base)

    age = (decision_dt - quote_dt).total_seconds()
    spread = _spread_percent(bid, ask)
    base["quote_age_seconds"] = age
    base["spread_percent"] = spread

    if age < 0:
        return QuoteRecord(status="FUTURE", reason_code="quote_after_decision", **base)
    if age > rule.max_quote_age_seconds:
        return QuoteRecord(status="STALE", reason_code="quote_stale", **base)
    if spread > rule.max_spread_percent:
        return QuoteRecord(status="WIDE_SPREAD", reason_code="spread_too_wide", **base)

    return QuoteRecord(status="OK", reason_code="quote_retained", **base)


def build_quote_manifest(
    files: Mapping[str, bytes],
    *,
    rule: QuoteRetentionRule,
    rule_sha256: str,
) -> dict[str, object]:
    """Build a reproducible manifest over canonical quote JSONL byte payloads."""

    if not _valid_sha256(rule_sha256):
        raise ValueError("rule_sha256 must be a 64-character hexadecimal SHA-256")
    if not files:
        raise ValueError("at least one quote dataset file is required")

    entries: list[dict[str, object]] = []
    all_sources: set[str] = set()

    for path in sorted(files):
        if not path or path.startswith("/") or ".." in path.split("/"):
            raise ValueError(f"manifest path must be relative and traversal-free: {path!r}")
        payload = files[path]
        if not isinstance(payload, bytes):
            raise ValueError("manifest payloads must be bytes")

        rows = [line for line in payload.splitlines() if line.strip()]
        required_record_fields = set(QuoteRecord.__dataclass_fields__)
        for raw_line in rows:
            try:
                row = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path} contains invalid JSONL") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path} contains a non-object JSONL row")

            missing_record_fields = sorted(required_record_fields - set(row))
            extra_record_fields = sorted(set(row) - required_record_fields)
            if missing_record_fields or extra_record_fields:
                raise ValueError(
                    f"{path} row schema mismatch: "
                    f"missing={missing_record_fields}, extra={extra_record_fields}"
                )

            if row.get("rule_id") != rule.rule_id:
                raise ValueError(f"{path} row rule_id does not match manifest rule")
            if str(row.get("rule_sha256", "")).lower() != rule_sha256.lower():
                raise ValueError(f"{path} row rule_sha256 does not match manifest rule")

            if row.get("status") not in {
                "OK",
                "MISSING",
                "STALE",
                "FUTURE",
                "INVALID",
                "WIDE_SPREAD",
            }:
                raise ValueError(f"{path} contains invalid quote status {row.get('status')!r}")

            source = row.get("source")
            try:
                parsed_source = QuoteSource(source)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{path} contains unsupported source {source!r}") from exc
            if parsed_source not in rule.allowed_sources:
                raise ValueError(f"{path} contains source not allowed by rule: {source!r}")
            all_sources.add(parsed_source.value)

        entries.append(
            {
                "path": path,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "row_count": len(rows),
            }
        )

    return {
        "manifest_version": 1,
        "rule_id": rule.rule_id,
        "rule_sha256": rule_sha256.lower(),
        "max_quote_age_seconds": rule.max_quote_age_seconds,
        "max_spread_percent": rule.max_spread_percent,
        "sources": sorted(all_sources),
        "files": entries,
    }
