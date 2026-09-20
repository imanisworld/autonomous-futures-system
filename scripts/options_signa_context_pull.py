#!/usr/bin/env python3
"""Read-only Signa context pull for the options evidence inbox.

This is not a scheduler daemon and has no scanner, strategy, risk, broker,
order, or execution authority. It writes only to options_signa_context.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Iterable, Sequence

from alert_ranker.config import ScannerConfig, load_config
from alert_ranker.session_calendar import nyse_session_for
from alert_ranker.signa_context_store import SHARED_PROXY_SYMBOLS, SignaContextStore
from sources.signa_discovery import SignaDiscoveryClient, records_from_direct_response

DEFAULT_INCLUDE = (
    "scan",
    "action_card",
    "enhanced_signal",
    "options_flow",
    "dark_pool",
    "market_tide",
    "signal_index",
    "congress_flow",
)


def market_is_open(now: datetime) -> bool:
    current = now.astimezone(timezone.utc)
    session = nyse_session_for(current.date())
    return bool(session and session.open <= current <= session.close)


def build_symbols(base: Iterable[str], *, include_shared_proxies: bool = True, limit: int = 50) -> list[str]:
    symbols: list[str] = []
    for value in base:
        symbol = str(value).strip().upper()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    if include_shared_proxies:
        for symbol in sorted(SHARED_PROXY_SYMBOLS):
            if symbol not in symbols:
                symbols.append(symbol)
    return symbols[: max(1, int(limit))]


def pull_context(
    *,
    cfg: ScannerConfig,
    symbols: Sequence[str],
    include: set[str],
    timeframe: str,
    now: datetime,
    client: SignaDiscoveryClient | None = None,
) -> dict[str, object]:
    if not cfg.signa_api_key_configured:
        return {"ok": False, "error": "signa_api_key_not_configured", "stored_rows": 0}
    signa = client or SignaDiscoveryClient(base_url=cfg.signa_base_url, timeout=cfg.signa_timeout_seconds)
    rows: list[dict[str, object]] = []
    endpoint_results: list[dict[str, object]] = []

    def add_rows(source: str, response, symbol: str | None = None) -> None:
        endpoint_results.append(
            {
                "source": source,
                "symbol": symbol,
                "endpoint": response.endpoint,
                "ok": response.ok,
                "status_code": response.status_code,
                "error": response.error,
                "cached": response.cached,
                "backoff_active": response.backoff_active,
            }
        )
        rows.extend(records_from_direct_response(source, response, symbol=symbol))

    if "scan" in include:
        add_rows("scan", signa.scan(symbols=symbols, timeframe=timeframe))
    if "signal_index" in include:
        add_rows("signal_index", signa.signal_index())
    if "market_tide" in include:
        add_rows("market_tide", signa.market_tide())

    for symbol in symbols:
        if "action_card" in include:
            add_rows("action_card", signa.action_card(symbol, timeframe=timeframe), symbol)
        if "enhanced_signal" in include:
            add_rows("enhanced_signal", signa.enhanced_signal(symbol, timeframe=timeframe), symbol)
        if "options_flow" in include:
            add_rows("options_flow", signa.options_flow(symbol), symbol)
        if "dark_pool" in include:
            add_rows("dark_pool", signa.dark_pool(symbol), symbol)
        if "congress_flow" in include:
            add_rows("congress_flow", signa.congress_flow(symbol), symbol)
        if "gex" in include:
            add_rows("gex", signa.gex(symbol), symbol)

    store = SignaContextStore(cfg.sqlite_path)
    ids = store.record_many(rows, timestamp=now)
    return {
        "ok": True,
        "advisory_only": True,
        "observation_only": True,
        "trade_authority": False,
        "symbols": list(symbols),
        "requested_rows": len(rows),
        "stored_rows": len(set(ids)),
        "ids": ids,
        "endpoint_results": endpoint_results,
    }


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pull read-only Signa context into options_signa_context")
    parser.add_argument("--symbols", default="", help="Comma-separated symbols. Defaults to OPTIONS_SCANNER_WATCHLIST.")
    parser.add_argument("--timeframe", default="1d")
    parser.add_argument("--include", default=",".join(DEFAULT_INCLUDE))
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--no-shared-proxies", action="store_true")
    parser.add_argument("--include-gex", action="store_true", help="Record explicit unresolved GEX context rows.")
    parser.add_argument("--force", action="store_true", help="Run even outside NYSE regular market hours.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    cfg = load_config()
    now = datetime.now(timezone.utc)
    if not args.force and not market_is_open(now):
        print(json.dumps({"ok": True, "skipped": "market_closed", "stored_rows": 0}, sort_keys=True))
        return 0

    base_symbols = [s for s in args.symbols.split(",") if s.strip()] if args.symbols else cfg.watchlist
    symbols = build_symbols(base_symbols, include_shared_proxies=not args.no_shared_proxies, limit=args.limit)
    include = {item.strip() for item in args.include.split(",") if item.strip()}
    if args.include_gex:
        include.add("gex")
    result = pull_context(cfg=cfg, symbols=symbols, include=include, timeframe=args.timeframe, now=now)
    print(json.dumps(result, sort_keys=True, default=str))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
