#!/usr/bin/env python3
"""Observation-only setup-capture collector (oneshot, 1-minute timer).

Reuses the 122 prospective pattern: append-only fsynced JSONL, journal replay,
IEX first-boundary for equities, delayed SIP reconcile, capture lag <= 120 s
measured against the SIP-reconciled cross. Does not import Signa, GEX, chain,
paper_v1, Discord, or the scanner.

SPX uses Public historicdata/INDEX/SPX ONE_MINUTE bars (bar resolution, never
trade-exact). Alpaca is never queried for SPX.

Integrity note: the 122 unit WorkingDirectory is /root/autonomous-futures-system
(not the pinned release). This unit journals to an absolute path under
/root/afs-shared/logs so ProtectSystem=strict cannot lose Friday WATCHING.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from dotenv import load_dotenv

from alert_ranker.causal_bars import MINUTE_1, MINUTE_30, Bar
from alert_ranker.config import load_config, resolve_alpaca_credentials
from alert_ranker.market_data import PublicMarketDataClient
from alert_ranker.public_chart_bars import parse_complete_grid_bars
from alert_ranker.setup_capture import (
    DEFAULT_JOURNAL,
    DEFAULT_RAW_TRADE_DIR,
    IndexMinuteBar,
    TapePrint,
    quote_instrument_type,
    watcher_universe,
)
from alert_ranker.setup_capture_engine import SetupCaptureEngine, alpaca_spx_forbidden
from alert_ranker.setup_capture_store import SetupCaptureJournal

COLLECTOR_ID = "OPTIONS_SETUP_CAPTURE_COLLECTOR"


def _clock_offset_seconds() -> float:
    try:
        proc = subprocess.run(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 0.0
    synced = (proc.stdout or "").strip().lower()
    if synced and synced not in {"yes", "1", "true"}:
        return 31.0
    return 0.0


def _as_prints(trades: Sequence[Any], feed: str) -> list[TapePrint]:
    out: list[TapePrint] = []
    for trade in trades:
        ts = datetime.fromisoformat(str(trade.timestamp).replace("Z", "+00:00"))
        out.append(
            TapePrint(
                timestamp=ts,
                timestamp_ns=int(trade.timestamp_ns),
                price=float(trade.price),
                trade_id=str(trade.trade_id),
                feed=feed,
                conditions=tuple(trade.conditions),
                tape=str(trade.tape),
                exchange=str(trade.exchange),
            )
        )
    return out


async def _fetch_week_bars(cfg, symbol: str, now: datetime) -> list[Bar]:
    async with PublicMarketDataClient(cfg) as pub:
        itype = quote_instrument_type(symbol)
        payload = await pub.fetch_historic_chart(symbol, instrument_type=itype, period="WEEK")
        if not payload:
            payload = await pub.fetch_historic_chart(symbol, instrument_type=itype, period="DAY")
        if not payload:
            return []
        return list(parse_complete_grid_bars(payload, timeframe=MINUTE_30, decision_ts=now))


async def _fetch_index_minutes(cfg, symbol: str, start: datetime, end: datetime) -> list[IndexMinuteBar]:
    async with PublicMarketDataClient(cfg) as pub:
        payload = await pub.fetch_historic_chart(
            symbol, instrument_type="INDEX", period="DAY", aggregation="ONE_MINUTE"
        )
        if not payload:
            return []
        bars = parse_complete_grid_bars(payload, timeframe=MINUTE_1, decision_ts=end)
        return [
            IndexMinuteBar(
                start=bar.start_utc,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                available_at=bar.start_utc + timedelta(minutes=1),
            )
            for bar in bars
            if start <= bar.start_utc < end
        ]


def _live_run(args: argparse.Namespace) -> dict[str, Any]:
    if args.env_file:
        load_dotenv(args.env_file, override=True)
    cfg = load_config()
    journal = SetupCaptureJournal(args.journal)
    key, secret = resolve_alpaca_credentials()
    iex_provider = None
    sip_provider = None
    if key and secret:
        from scripts.options_122_iex_provisional_audit import HistoricalTradeProvider

        iex_provider = HistoricalTradeProvider(cfg.alpaca_data_base_url, key, secret, "iex")
        sip_provider = HistoricalTradeProvider(cfg.alpaca_data_base_url, key, secret, "sip")

    def bar_oracle(symbol: str, now: datetime) -> list[Bar]:
        return asyncio.run(_fetch_week_bars(cfg, symbol, now))

    def iex_prints(symbol: str, start: datetime, end: datetime) -> list[TapePrint]:
        alpaca_spx_forbidden(symbol)
        if iex_provider is None:
            return []
        trades = asyncio.run(iex_provider.fetch_trades(symbol=symbol, start=start, end=end))
        return _as_prints(trades, "iex")

    def sip_prints(symbol: str, start: datetime, end: datetime) -> list[TapePrint]:
        alpaca_spx_forbidden(symbol)
        if sip_provider is None:
            return []
        trades = asyncio.run(sip_provider.fetch_trades(symbol=symbol, start=start, end=end))
        return _as_prints(trades, "sip")

    def index_minutes(symbol: str, start: datetime, end: datetime) -> list[IndexMinuteBar]:
        return asyncio.run(_fetch_index_minutes(cfg, symbol, start, end))

    engine = SetupCaptureEngine(
        journal=journal,
        bar_oracle=bar_oracle,
        iex_prints=iex_prints,
        sip_prints=sip_prints,
        index_minutes=index_minutes,
        clock_offset_s=_clock_offset_seconds(),
        enabled=bool(getattr(cfg, "setup_capture_enabled", True)),
    )
    now = datetime.now(timezone.utc)
    summary = engine.run(now=now)
    summary["collector_id"] = COLLECTOR_ID
    summary["universe"] = list(watcher_universe())
    summary["journal"] = str(journal.path)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file")
    parser.add_argument("--journal", default=DEFAULT_JOURNAL)
    parser.add_argument("--raw-trade-dir", default=DEFAULT_RAW_TRADE_DIR)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not Path(args.journal).is_absolute():
        raise SystemExit("setup_capture_journal_must_be_absolute")
    print(json.dumps(_live_run(args), indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
