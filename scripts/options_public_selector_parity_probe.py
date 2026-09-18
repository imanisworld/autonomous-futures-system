#!/usr/bin/env python3
"""Read-only Public -> canonical selector parity probe.

Fetches current Public market data only, converts supplied option chains through
the canonical serialized selector-input bridge, and proves replay/forward
selection parity on the exact same bytes. It writes nothing and makes no
broker, account-management, or order call.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alert_ranker.config import load_config
from alert_ranker.market_data import PublicMarketDataClient
from alert_ranker.options_selector_input import serialized_selector_input_from_option_chains
from options_manager.contracts import (
    selection_input_from_json,
    selection_input_json,
    selection_result_json,
    select_contract_from_serialized_input,
    selector_rule_from_mapping,
)

RULE_PATH = ROOT / "options_manager" / "contracts" / "selector_rule_v1.json"


def choose_probe_expirations(
    expirations: Iterable[str],
    *,
    today: date,
    min_dte: int,
    preferred_min_dte: int,
    limit: int = 2,
) -> list[str]:
    """Choose enough expirations to exercise the frozen nearest-expiration rule."""
    parsed: list[tuple[int, str]] = []
    for raw in expirations:
        try:
            expiry = date.fromisoformat(str(raw))
        except ValueError:
            continue
        dte = (expiry - today).days
        if dte >= min_dte:
            parsed.append((dte, expiry.isoformat()))

    parsed.sort()
    preferred = [expiration for dte, expiration in parsed if dte >= preferred_min_dte]
    if preferred:
        return preferred[:limit]
    return [expiration for _, expiration in parsed[:limit]]


def evaluate_capture(
    *,
    chains,
    rule,
    rule_sha256: str,
    decision_ts: str,
    underlying_price: float,
    direction: str,
) -> dict[str, object]:
    payload = serialized_selector_input_from_option_chains(
        chains,
        rule_sha256=rule_sha256,
        decision_ts=decision_ts,
        underlying_price=underlying_price,
        direction=direction,
    )
    parsed = selection_input_from_json(payload)
    round_trip = selection_input_json(parsed).encode("utf-8")
    replay = select_contract_from_serialized_input(rule=rule, payload=payload)
    forward = select_contract_from_serialized_input(rule=rule, payload=payload)
    replay_bytes = selection_result_json(replay).encode("utf-8")
    forward_bytes = selection_result_json(forward).encode("utf-8")


    return {
        "direction": direction,
        "selector_input_sha256": hashlib.sha256(payload).hexdigest(),
        "selector_input_rows": len(parsed.chain),
        "input_round_trip_byte_stable": round_trip == payload,
        "replay_forward_result_byte_parity": replay_bytes == forward_bytes,
        "selection": asdict(replay),
    }


def direction_results_prove_parity(results: list[dict[str, object]]) -> bool:
    """Parity is valid for deterministic SELECTED or NO_CONTRACT outcomes."""
    return bool(results) and all(
        result["input_round_trip_byte_stable"]
        and result["replay_forward_result_byte_parity"]
        for result in results
    )


def _load_rule():
    rule_bytes = RULE_PATH.read_bytes()
    mapping = json.loads(rule_bytes.decode("utf-8"))
    return (
        selector_rule_from_mapping(mapping),
        hashlib.sha256(rule_bytes).hexdigest(),
    )


async def _run(tickers: list[str]) -> tuple[dict[str, object], int]:
    cfg = load_config()
    if cfg.market_data_provider != "public":
        return {
            "verdict": "BLOCKED",
            "reason": "OPTIONS_MARKET_DATA_PROVIDER is not public",
        }, 2

    try:
        rule, rule_sha = _load_rule()
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {
            "verdict": "BLOCKED",
            "reason": f"frozen selector rule unavailable: {type(exc).__name__}",
        }, 2

    rows: list[dict[str, object]] = []

    async with PublicMarketDataClient(cfg) as client:
        for raw_ticker in tickers:
            ticker = raw_ticker.strip().upper()
            if not ticker:
                continue
            snapshot = await client.fetch_market_snapshot(ticker)
            expirations = await client.fetch_option_expirations(ticker)
            chosen = choose_probe_expirations(
                expirations,
                today=datetime.now(timezone.utc).date(),
                min_dte=rule.min_dte,
                preferred_min_dte=rule.preferred_min_dte,
            )


            if snapshot.price is None or not chosen:
                rows.append({
                    "ticker": ticker,
                    "status": "BLOCKED",
                    "reason": client.last_error or "missing_underlying_price_or_eligible_expiration",
                    "expiration_candidates": chosen,
                })
                continue

            chains = [await client.fetch_option_chain(ticker, expiration) for expiration in chosen]
            if any(chain.error for chain in chains):
                rows.append({
                    "ticker": ticker,
                    "status": "BLOCKED",
                    "reason": "option_chain_error",
                    "expiration_candidates": chosen,
                    "chain_errors": [chain.error for chain in chains],
                })
                continue

            # Decision time is after the chain snapshot has been received.
            # Timestamping before fetch would incorrectly classify newly fetched
            # quotes as future data.
            decision_ts = datetime.now(timezone.utc).isoformat()
            direction_results: list[dict[str, object]] = []
            try:
                for direction in ("CALL", "PUT"):
                    direction_results.append(
                        evaluate_capture(
                            chains=chains,
                            rule=rule,
                            rule_sha256=rule_sha,
                            decision_ts=decision_ts,
                            underlying_price=float(snapshot.price),
                            direction=direction,
                        )
                    )
            except (TypeError, ValueError) as exc:
                rows.append({
                    "ticker": ticker,
                    "status": "BLOCKED",
                    "reason": f"selector_input_invalid:{type(exc).__name__}",
                    "expiration_candidates": chosen,
                })
                continue


            passed = direction_results_prove_parity(direction_results)
            rows.append({
                "ticker": ticker,
                "status": "PROVEN_FOR_CAPTURE" if passed else "BLOCKED",
                "expiration_candidates": chosen,
                "underlying_price": snapshot.price,
                "directions": direction_results,
            })

    passed = bool(rows) and all(row["status"] == "PROVEN_FOR_CAPTURE" for row in rows)
    report = {
        "verdict": "PROVEN_FOR_CAPTURE" if passed else "BLOCKED",
        "scope": "current Public market-data capture only",
        "selector_rule_sha256": rule_sha,
        "tickers": [row["ticker"] for row in rows],
        "results": rows,
        "claims_not_made": [
            "historical quote coverage",
            "future Public response guarantees",
            "production scanner activation",
            "DEMO eligibility",
            "strategy validity",
        ],
    }
    return report, 0 if passed else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ticker",
        action="append",
        dest="tickers",
        help="Ticker to probe; repeatable. Defaults to configured scanner watchlist.",
    )

    args = parser.parse_args()
    cfg = load_config()
    tickers = args.tickers or list(cfg.watchlist)
    report, exit_code = asyncio.run(_run(tickers))
    print(json.dumps(report, sort_keys=True, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
