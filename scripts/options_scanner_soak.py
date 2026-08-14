"""Offline soak test for the advisory options scanner (Phase-3 memory evidence).

Simulates the live workload with no network: a journal seeded to the reported
live backlog size, then repeated scheduler cycles (watchlist scan + candidate
resolution) interleaved with dashboard polling (/status, /terminal,
shadow_summary). Prints RSS at intervals so retention is observable.

Usage: python3 scripts/options_scanner_soak.py [cycles] [seed_rows]
"""

from __future__ import annotations

import asyncio
import resource
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alert_ranker.config import ScannerConfig
from alert_ranker.discord import DiscordAlerter
from alert_ranker.scanner import OptionsScanner
from alert_ranker.scorer import ScoreResult
from alert_ranker.storage import ScanStorage
from alert_ranker.tastytrade_client import MarketSnapshot

WATCHLIST = ["AAPL", "MSFT", "NVDA", "TSLA", "SPY", "QQQ"]
SCAN_TIME = datetime(2026, 8, 5, 10, 0, tzinfo=ZoneInfo("America/New_York"))


def rss_mb() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
    return usage / divisor


class StubMarketData:
    provider_name = "stub"
    last_error = None

    async def fetch_market_snapshot(self, ticker: str) -> MarketSnapshot:
        return MarketSnapshot(
            ticker.upper(),
            price=500.0,
            volume=1_000_000,
            bid=499.9,
            ask=500.1,
            raw={"provider": "stub", "endpoint": "quotes", "quote": {"last": "500.0"}},
        )


def seed_journal(storage: ScanStorage, rows: int) -> None:
    raw = {
        "ticker": "SPY",
        "pattern": "N/A",
        "market_data_error": "HTTPStatusError",
        "signa_grade": "B",
        "signa_score": 71,
        "vwap": None,
        "ema20": None,
    }
    result = ScoreResult("SPY", "UNKNOWN", 0, "N/A", {}, raw)
    for _ in range(rows):
        scan_id = storage.record_scan(
            result, source="scheduled", alert_sent=False, alert_suppression_reason="", timestamp=SCAN_TIME
        )
        storage.record_shadow_setup(
            result,
            scan_id=scan_id,
            setup_inputs=dict(raw),
            provider_snapshot={"provider": "public", "error": "HTTPStatusError"},
            timestamp=SCAN_TIME,
        )


async def main(cycles: int, seed_rows: int) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="options_soak_"))
    cfg = ScannerConfig(
        market_data_provider="public",
        tastytrade_username="",
        tastytrade_password="",
        tastytrade_base_url="https://api.tastyworks.com",
        public_api_key_configured=True,
        public_base_url="https://api.public.com",
        alpaca_api_key_configured=False,
        alpaca_secret_key_configured=False,
        alpaca_paper=True,
        alpaca_data_base_url="https://data.alpaca.markets",
        port=8010,
        discord_webhook_url="",
        watchlist=WATCHLIST,
        interval_minutes=5,
        sqlite_path=tmp / "soak.sqlite",
        public_account_id="ACCSOAK",
    )
    storage = ScanStorage(cfg.sqlite_path)
    print(f"seeding {seed_rows} legacy journal rows (reported live backlog scale)...")
    seed_journal(storage, seed_rows)
    scanner = OptionsScanner(cfg, StubMarketData(), storage, DiscordAlerter(cfg, storage))
    print(f"baseline RSS after seed: {rss_mb():.1f} MB")

    for cycle in range(1, cycles + 1):
        await scanner.scan_watchlist(source="scheduled", now=SCAN_TIME)
        await scanner.resolve_open_candidates(now=SCAN_TIME)
        # Dashboard polling load: three endpoints per cycle.
        scanner.status()
        scanner.terminal_state()
        storage.shadow_summary()
        if cycle % max(1, cycles // 10) == 0:
            print(f"cycle {cycle:4d}: peak RSS {rss_mb():.1f} MB")

    peak = rss_mb()
    print(f"final peak RSS: {peak:.1f} MB over {cycles} cycles x {len(WATCHLIST)} tickers")
    print("PASS (< 350 MB)" if peak < 350 else "FAIL (>= 350 MB)")


if __name__ == "__main__":
    cycles = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    seed_rows = int(sys.argv[2]) if len(sys.argv) > 2 else 6853
    asyncio.run(main(cycles, seed_rows))
