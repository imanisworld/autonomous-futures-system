"""Read-only Public option-chain timestamp coverage probe.

This script uses the existing read-only Public market-data client and prints a
single JSON report to stdout. It does not place orders, call trading/account
endpoints, write files, mutate configuration, or restart any service.

Purpose: prove what the provider actually returned for executable bid/ask
timestamps during this capture. It does not claim historical coverage or
strategy validity.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Iterable

from alert_ranker.config import load_config
from alert_ranker.market_data import (
    PUBLIC_OPTION_CHAIN_SOURCE,
    OptionChain,
    OptionContractQuote,
    PublicMarketDataClient,
)

ROOT = Path(__file__).resolve().parents[1]
QUOTE_RULE_PATH = ROOT / "options_manager" / "quotes" / "quote_retention_rule_v1.json"
SELECTOR_RULE_PATH = ROOT / "options_manager" / "contracts" / "selector_rule_v1.json"


@dataclass(frozen=True)
class ChainTimestampSummary:
    ticker: str
    expiration: str | None
    received_at: str
    total_contracts: int
    quoted_contracts: int
    executable_timestamp_count: int
    missing_executable_timestamp_count: int
    fresh_executable_timestamp_count: int
    stale_executable_timestamp_count: int
    future_executable_timestamp_count: int
    invalid_executable_timestamp_count: int
    min_age_seconds: float | None
    max_age_seconds: float | None
    all_quoted_have_executable_timestamp: bool
    all_executable_timestamps_fresh: bool
    source_values: tuple[str, ...]
    error: str | None = None


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def choose_probe_expiration(
    expirations: Iterable[str],
    *,
    today: date,
    min_dte: int,
    preferred_min_dte: int,
) -> str | None:
    """Mirror the selector's expiration bands without selecting a contract."""

    parsed: list[tuple[str, int]] = []
    for raw in expirations:
        try:
            expiry = date.fromisoformat(str(raw))
        except ValueError:
            continue
        dte = (expiry - today).days
        if dte >= min_dte:
            parsed.append((expiry.isoformat(), dte))
    if not parsed:
        return None
    preferred = sorted((item for item in parsed if item[1] >= preferred_min_dte), key=lambda x: x[1])
    if preferred:
        return preferred[0][0]
    return min(parsed, key=lambda x: x[1])[0]


def summarize_chain_timestamps(
    chain: OptionChain,
    *,
    received_at: datetime,
    max_quote_age_seconds: int,
) -> ChainTimestampSummary:
    """Summarize executable bid/ask timestamp coverage for one returned chain."""

    if received_at.tzinfo is None or received_at.utcoffset() is None:
        raise ValueError("received_at must be timezone-aware")

    contracts: tuple[OptionContractQuote, ...] = tuple(chain.calls) + tuple(chain.puts)
    quoted = [
        contract
        for contract in contracts
        if contract.bid is not None
        and contract.ask is not None
        and contract.bid > 0
        and contract.ask >= contract.bid
    ]

    ages: list[float] = []
    fresh = stale = future = invalid = 0
    executable = 0
    sources: set[str] = set()

    for contract in quoted:
        if contract.source:
            sources.add(str(contract.source))
        raw_ts = contract.quote_timestamp
        if raw_ts is None:
            continue
        parsed = _parse_ts(raw_ts)
        if parsed is None:
            invalid += 1
            continue
        executable += 1
        age = (received_at - parsed).total_seconds()
        ages.append(age)
        if age < 0:
            future += 1
        elif age > max_quote_age_seconds:
            stale += 1
        else:
            fresh += 1

    quoted_count = len(quoted)
    missing = quoted_count - executable
    return ChainTimestampSummary(
        ticker=chain.underlying,
        expiration=chain.expiration,
        received_at=received_at.astimezone(timezone.utc).isoformat(),
        total_contracts=len(contracts),
        quoted_contracts=quoted_count,
        executable_timestamp_count=executable,
        missing_executable_timestamp_count=missing,
        fresh_executable_timestamp_count=fresh,
        stale_executable_timestamp_count=stale,
        future_executable_timestamp_count=future,
        invalid_executable_timestamp_count=invalid,
        min_age_seconds=min(ages) if ages else None,
        max_age_seconds=max(ages) if ages else None,
        all_quoted_have_executable_timestamp=(quoted_count > 0 and executable == quoted_count),
        all_executable_timestamps_fresh=(executable > 0 and fresh == executable),
        source_values=tuple(sorted(sources)),
        error=chain.error,
    )


def _load_rule_numbers() -> tuple[int, int, int]:
    quote_rule = json.loads(QUOTE_RULE_PATH.read_text())
    selector_rule = json.loads(SELECTOR_RULE_PATH.read_text())
    return (
        int(quote_rule["max_quote_age_seconds"]),
        int(selector_rule["min_dte"]),
        int(selector_rule["preferred_min_dte"]),
    )


async def _run(tickers: list[str]) -> tuple[dict[str, object], int]:
    cfg = load_config()
    if cfg.market_data_provider != "public":
        return (
            {
                "verdict": "BLOCKED",
                "reason": "OPTIONS_MARKET_DATA_PROVIDER is not public",
                "provider": cfg.market_data_provider,
            },
            2,
        )

    try:
        max_age, min_dte, preferred_min_dte = _load_rule_numbers()
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        return (
            {
                "verdict": "BLOCKED",
                "reason": f"frozen rule load failed: {type(exc).__name__}",
            },
            2,
        )

    summaries: list[ChainTimestampSummary] = []

    async with PublicMarketDataClient(cfg) as client:
        for ticker in tickers:
            symbol = ticker.strip().upper()
            if not symbol:
                continue
            expirations = await client.fetch_option_expirations(symbol)
            expiration = choose_probe_expiration(
                expirations,
                today=datetime.now(timezone.utc).date(),
                min_dte=min_dte,
                preferred_min_dte=preferred_min_dte,
            )
            if expiration is None:
                summaries.append(
                    ChainTimestampSummary(
                        ticker=symbol,
                        expiration=None,
                        received_at=datetime.now(timezone.utc).isoformat(),
                        total_contracts=0,
                        quoted_contracts=0,
                        executable_timestamp_count=0,
                        missing_executable_timestamp_count=0,
                        fresh_executable_timestamp_count=0,
                        stale_executable_timestamp_count=0,
                        future_executable_timestamp_count=0,
                        invalid_executable_timestamp_count=0,
                        min_age_seconds=None,
                        max_age_seconds=None,
                        all_quoted_have_executable_timestamp=False,
                        all_executable_timestamps_fresh=False,
                        source_values=(),
                        error=client.last_error or "no_expiration_in_selector_dte_band",
                    )
                )
                continue

            chain = await client.fetch_option_chain(symbol, expiration)
            summaries.append(
                summarize_chain_timestamps(
                    chain,
                    received_at=datetime.now(timezone.utc),
                    max_quote_age_seconds=max_age,
                )
            )

    passed = bool(summaries) and all(
        summary.error is None
        and summary.quoted_contracts > 0
        and summary.all_quoted_have_executable_timestamp
        and summary.all_executable_timestamps_fresh
        and summary.future_executable_timestamp_count == 0
        and summary.invalid_executable_timestamp_count == 0
        and summary.source_values == (PUBLIC_OPTION_CHAIN_SOURCE,)
        for summary in summaries
    )
    report = {
        "verdict": "PROVEN_FOR_CAPTURE" if passed else "BLOCKED",
        "scope": "current Public option-chain capture only",
        "provider": "public",
        "max_quote_age_seconds": max_age,
        "tickers": [summary.ticker for summary in summaries],
        "summaries": [asdict(summary) for summary in summaries],
        "claims_not_made": [
            "historical quote coverage",
            "every future Public response will contain timestamps",
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
        help="Ticker to probe; repeatable. Defaults to the configured scanner watchlist.",
    )
    args = parser.parse_args()
    cfg = load_config()
    tickers = args.tickers or list(cfg.watchlist)
    report, exit_code = asyncio.run(_run(tickers))
    print(json.dumps(report, sort_keys=True, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
