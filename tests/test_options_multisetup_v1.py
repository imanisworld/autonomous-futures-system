from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from alert_ranker.config import ScannerConfig
from alert_ranker.discord import DiscordAlerter
from alert_ranker.market_data import OptionChain, OptionContractQuote
from alert_ranker.scanner import OptionsScanner
from alert_ranker.storage import ScanStorage
from alert_ranker.tastytrade_client import MarketSnapshot


NOW = datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)


class PaperMarket:
    provider_name = "public"
    last_error = None

    async def fetch_market_snapshot(self, ticker: str) -> MarketSnapshot:
        return MarketSnapshot(ticker.upper(), price=100.0, volume=1_000_000)

    async def fetch_option_expirations(self, ticker: str) -> list[str]:
        return ["2026-10-23"]

    async def fetch_option_chain(self, ticker: str, expiration: str | None = None) -> OptionChain:
        contract = OptionContractQuote(
            symbol="AAPL261023C00100000",
            option_type="CALL",
            strike=100.0,
            bid=1.90,
            ask=2.00,
            mid=1.95,
            last=1.95,
            volume=600.0,
            open_interest=2000.0,
            delta=0.45,
            implied_volatility=0.30,
        )
        return OptionChain("AAPL", expiration, calls=(contract,), puts=(), error=None)


def _config(tmp_path) -> ScannerConfig:
    return ScannerConfig(
        market_data_provider="public",
        tastytrade_username="",
        tastytrade_password="",
        tastytrade_base_url="https://api.tastyworks.com",
        public_api_key_configured=True,
        public_base_url="https://api.public.com",
        port=8010,
        discord_webhook_url="",
        watchlist=["AAPL"],
        interval_minutes=5,
        sqlite_path=tmp_path / "options.sqlite",
    )


def _daily(setup_type: str, pattern: str) -> dict:
    return {
        "paper_candidate_id": f"1D:{setup_type}",
        "setup_type": setup_type,
        "setup_timeframe": "1D",
        "timeframe": "1D",
        "pattern": pattern,
        "strat_sequence": pattern,
        "direction": "LONG",
        "setup_direction": "CALL",
        "setup_status": "TRIGGERED",
        "setup_reason_code": "daily_paper_evidence_setup_proven",
        "setup_proof_status": "VALID",
        "setup_market_status": "VALID",
        "underlying_invalidation": 95.0,
        "stop": 95.0,
        "target": 110.0,
        "target_1": 110.0,
        "target_2": 115.0,
        "trade_proof_status": "INCOMPLETE",
        "trade_proof_reason": "event_risk_unavailable;flip_context_unavailable",
    }


def test_same_contract_can_be_collected_for_two_distinct_daily_setups(tmp_path):
    cfg = _config(tmp_path)
    storage = ScanStorage(cfg.sqlite_path)
    scanner = OptionsScanner(cfg, PaperMarket(), storage, DiscordAlerter(cfg, storage))

    async def fake_normalized(ticker, context, now):
        # Primary/legacy lane deliberately has no setup. Daily candidates are
        # additive evidence and must not overwrite the primary API result.
        return {
            "ticker": "AAPL",
            "pattern": "N/A",
            "price": 100.0,
            "vwap": 99.0,
            "ema20": 98.0,
            "volume_ratio": 1.5,
            "iv_rank": 20.0,
            "setup_status": "NO_TRADE",
            "paper_setup_candidates": [
                _daily("DAILY_222_CONTINUATION", "strat_222_continuation"),
                _daily("DAILY_322_CONTINUATION", "strat_322_continuation"),
            ],
        }

    scanner._build_normalized_data = fake_normalized
    primary = asyncio.run(
        scanner.scan_ticker("AAPL", source="scheduled", context={}, now=NOW)
    )

    assert primary.result.pattern == "N/A"
    assert primary.shadow_id == 0

    rows = storage.latest_shadow_setups(limit=10)
    assert len(rows) == 2
    by_setup = {row.selected_contract["setup_type"]: row for row in rows}
    assert set(by_setup) == {"DAILY_222_CONTINUATION", "DAILY_322_CONTINUATION"}

    first = by_setup["DAILY_222_CONTINUATION"].selected_contract
    second = by_setup["DAILY_322_CONTINUATION"].selected_contract
    assert first["contract"] == second["contract"] == "AAPL261023C00100000"
    assert first["option_contract_key"] == second["option_contract_key"]
    assert first["contract_key"] != second["contract_key"]
    assert first["setup_timeframe"] == second["setup_timeframe"] == "1D"
    assert first["planned_risk_dollars"] == 50.0
    assert second["planned_risk_dollars"] == 50.0

    # Risk is serial, not position-count based: candidate 2 sees candidate 1's
    # $50 planned risk and still fits under the frozen $1,000 aggregate cap.
    projected = sorted(row.selected_contract["projected_aggregate_open_planned_risk"] for row in rows)
    assert projected == [50.0, 100.0]
