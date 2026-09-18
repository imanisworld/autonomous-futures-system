"""Prospective decision-time options selector evidence.

This module is evidence-only. It does not fetch market data, select a contract
for execution, submit an order, or change scanner policy. It receives the exact
already-fetched production inputs, serializes the canonical selector input, and
builds a byte-stable evidence envelope for later replay.

The current scanner still owns expiration selection separately. The envelope
therefore preserves both:
- the exact expiration list and production-chosen expiration; and
- canonical selector bytes for the chain that production actually evaluated.

That is sufficient to replay the current production decision without inventing
historical Greeks, open interest, quote timestamps, or underlying provenance.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from alert_ranker.market_data import OptionChain, OptionContractQuote
from alert_ranker.options_selector_input import serialized_selector_input_from_option_chains
from options_manager.contracts import selection_input_from_json

ROOT = Path(__file__).resolve().parents[1]
SELECTOR_RULE_PATH = ROOT / "options_manager" / "contracts" / "selector_rule_v1.json"
EVIDENCE_VERSION = 1


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), default=str) + "\n").encode(
        "utf-8"
    )


def _selector_rule_sha256(rule_path: Path = SELECTOR_RULE_PATH) -> str:
    return hashlib.sha256(rule_path.read_bytes()).hexdigest()


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
    rule_path: Path = SELECTOR_RULE_PATH,
) -> dict[str, Any]:
    """Build one replayable decision-time evidence envelope.

    Raises when the canonical selector input cannot be represented exactly.
    Callers may record that failure as DATA_BLOCKED, but must never synthesize
    missing fields.
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

    rule_sha256 = _selector_rule_sha256(rule_path)
    selector_payload = serialized_selector_input_from_option_chains(
        [chain],
        rule_sha256=rule_sha256,
        decision_ts=decision_ts,
        underlying_price=underlying_price,
        direction=selector_direction,
    )
    parsed_selector = selection_input_from_json(selector_payload)

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

    envelope: dict[str, Any] = {
        "evidence_version": EVIDENCE_VERSION,
        "status": "CAPTURED",
        "ticker": ticker.upper(),
        "production_direction": production_direction,
        "selector_direction": selector_direction,
        "decision_ts": decision_ts,
        "expiration_candidates": [str(item) for item in expirations],
        "chosen_expiration": chosen_expiration,
        "selector_rule_sha256": rule_sha256,
        "selector_input_sha256": hashlib.sha256(selector_payload).hexdigest(),
        "selector_input_json": selector_payload.decode("utf-8").rstrip("\n"),
        "selector_input_rows": len(parsed_selector.chain),
        "underlying": {
            "price": underlying_price,
            "snapshot": dict(underlying_snapshot or {}),
        },
        # Supplemental fields not present in the frozen selector schema but
        # required for provenance/audit (side timestamps, IV, source identity).
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
    """Attach the scanner's actual selection result and refresh evidence hash."""

    out = dict(evidence)
    out.pop("evidence_sha256", None)
    out["production_selection"] = dict(production_selection)
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
