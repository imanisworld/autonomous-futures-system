"""Prospective decision-time options selector evidence.

This module is evidence-only. It does not fetch market data, select a contract
for execution, submit an order, or change scanner policy. It receives the exact already-fetched production inputs and builds a byte-stable
production-selector evidence envelope for later replay.

The current scanner owns expiration and contract selection. The envelope
preserves the exact expiration candidates, production-chosen expiration,
underlying provenance and evaluated chain bytes needed to replay that decision.
The canonical/reference selector is deliberately not a runtime dependency.

That is sufficient to replay the current production decision without inventing
historical Greeks, open interest, quote timestamps, or underlying provenance.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Any, Mapping, Sequence

from alert_ranker.market_data import OptionChain, OptionContractQuote
from alert_ranker.options_production_selector_replay import (
    production_selection_matches_replay,
    production_selector_code_sha256,
    replay_production_selector,
)
EVIDENCE_VERSION = 3


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), default=str) + "\n").encode(
        "utf-8"
    )


def _quote_supplement(quote: OptionContractQuote, expiration: str) -> dict[str, Any]:
    return {
        "contract_id": quote.symbol,
        "expiration": expiration,
        "right": quote.option_type,
        "strike": quote.strike,
        "bid": quote.bid,
        "ask": quote.ask,
        "mid": quote.mid,
        "last": quote.last,
        "volume": quote.volume,
        "open_interest": quote.open_interest,
        "delta": quote.delta,
        "implied_volatility": quote.implied_volatility,
        "quote_timestamp": quote.quote_timestamp,
        "bid_timestamp": quote.bid_timestamp,
        "ask_timestamp": quote.ask_timestamp,
        "source": quote.source,
    }


def build_selector_evidence_capture(
    *,
    ticker: str,
    production_direction: str,
    expirations: Sequence[str],
    chosen_expiration: str,
    chain: OptionChain,
    decision_ts: str,
    underlying_price: float,
    underlying_snapshot: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Build one replayable decision-time evidence envelope.

    Raises when production replay evidence cannot be represented exactly.
    Callers may record that failure as DATA_BLOCKED, but must never synthesize
    missing fields. No options_manager/canonical-selector runtime dependency is
    required; canonical/reference analysis can be performed offline later.
    """

    selector_direction = "CALL" if production_direction == "LONG" else "PUT"
    if production_direction not in {"LONG", "SHORT"}:
        raise ValueError("production_direction must be LONG or SHORT")
    if chain.error:
        raise ValueError(f"option chain blocked: {chain.error}")
    expiration = str(chain.expiration or "")
    if not expiration or expiration != chosen_expiration:
        raise ValueError("chain expiration must match production chosen expiration")

    # Validate the decision timestamp independently so an invalid timestamp is
    # recorded as blocked rather than silently serialized.
    parsed = datetime.fromisoformat(decision_ts.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("decision_ts must be timezone-aware")

    supplements = [
        _quote_supplement(quote, expiration)
        for quote in (*chain.calls, *chain.puts)
    ]
    supplements.sort(
        key=lambda row: (
            str(row["expiration"]),
            str(row["right"]),
            repr(row["strike"]),
            str(row["contract_id"]),
        )
    )

    production_code_sha = production_selector_code_sha256()
    selector_input = {
        "selector_authority": "OPTIONS_PAPER_V1",
        "production_selector_code_sha256": production_code_sha,
        "ticker": ticker.upper(),
        "production_direction": production_direction,
        "decision_ts": decision_ts,
        "expiration_candidates": [str(item) for item in expirations],
        "chosen_expiration": chosen_expiration,
        "underlying_price": underlying_price,
        "chain": supplements,
    }
    selector_payload = _canonical_json_bytes(selector_input)

    envelope: dict[str, Any] = {
        "evidence_version": EVIDENCE_VERSION,
        "status": "CAPTURED",
        "ticker": ticker.upper(),
        "production_direction": production_direction,
        "selector_direction": selector_direction,
        "decision_ts": decision_ts,
        "expiration_candidates": [str(item) for item in expirations],
        "chosen_expiration": chosen_expiration,
        "selector_authority": "OPTIONS_PAPER_V1",
        "canonical_selector_role": "offline_reference_only_not_runtime_dependency",
        "production_selector_code_sha256": production_code_sha,
        "production_selector_input_sha256": hashlib.sha256(selector_payload).hexdigest(),
        "production_selector_input_json": selector_payload.decode("utf-8").rstrip("\n"),
        "production_selector_input_rows": len(supplements),
        # Compatibility aliases for existing evidence readers.
        "selector_input_sha256": hashlib.sha256(selector_payload).hexdigest(),
        "selector_input_json": selector_payload.decode("utf-8").rstrip("\n"),
        "selector_input_rows": len(supplements),
        "underlying": {
            "price": underlying_price,
            "snapshot": dict(underlying_snapshot or {}),
        },
        "chain_supplement": supplements,
    }
    envelope_bytes = _canonical_json_bytes(envelope)
    envelope["evidence_sha256"] = hashlib.sha256(envelope_bytes).hexdigest()
    return envelope


def finalize_selector_evidence(
    evidence: Mapping[str, Any],
    *,
    production_selection: Mapping[str, Any],
) -> dict[str, Any]:
    """Attach actual production selection and prove retained-input replay parity."""

    out = dict(evidence)
    out.pop("evidence_sha256", None)
    out["production_selection"] = dict(production_selection)

    if out.get("status") == "CAPTURED":
        replay = replay_production_selector(out)
        parity = production_selection_matches_replay(production_selection, replay)
        out["production_replay_result"] = replay
        out["production_replay_parity"] = parity
        if not parity:
            out["status"] = "DATA_BLOCKED"
            out["reason_code"] = "production_replay_mismatch"

    out["evidence_sha256"] = hashlib.sha256(_canonical_json_bytes(out)).hexdigest()
    return out


def blocked_selector_evidence(
    *,
    ticker: str,
    production_direction: str,
    decision_ts: str,
    reason_code: str,
) -> dict[str, Any]:
    """Return an explicit DATA_BLOCKED record; never fabricate missing inputs."""

    envelope: dict[str, Any] = {
        "evidence_version": EVIDENCE_VERSION,
        "status": "DATA_BLOCKED",
        "ticker": ticker.upper(),
        "production_direction": production_direction,
        "decision_ts": decision_ts,
        "reason_code": reason_code,
    }
    envelope["evidence_sha256"] = hashlib.sha256(_canonical_json_bytes(envelope)).hexdigest()
    return envelope
