"""Shadow-journal lifecycle tests: candidate formation, resolution, reconciliation.

Phase-2 required matrix: non-candidate scans, provider failures, formed
candidates, stop resolution, target resolution, expiration, cancellation,
restart recovery, duplicate prevention, append-only reconciliation.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from alert_ranker.config import ScannerConfig
from alert_ranker.discord import DiscordAlerter
from alert_ranker.lifecycle import classify_candidate, resolve_open_setup
from alert_ranker.scanner import OptionsScanner
from alert_ranker.scorer import ScoreResult
from alert_ranker.storage import ScanStorage
from alert_ranker.tastytrade_client import MarketSnapshot

NY = ZoneInfo("America/New_York")
OPEN_TIME = datetime(2026, 8, 5, 10, 0, tzinfo=NY)


class FakeMarketData:
    provider_name = "fake"

    def __init__(self, price: float | None = 101.0, error: str | None = None):
        self.price = price
        self.error = error
        self.last_error = error
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def fetch_market_snapshot(self, ticker: str) -> MarketSnapshot:
        self.calls += 1
        if self.error:
            return MarketSnapshot(ticker.upper(), error=self.error)
        return MarketSnapshot(ticker.upper(), price=self.price, volume=1_000_000)


def make_config(tmp_path: Path) -> ScannerConfig:
    return ScannerConfig(
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
        watchlist=["SPY"],
        interval_minutes=5,
        sqlite_path=tmp_path / "options_scanner.sqlite",
        public_account_id="ACC12345",
    )


def make_scanner(
    tmp_path: Path, market_data: FakeMarketData | None = None
) -> tuple[OptionsScanner, ScanStorage]:
    cfg = make_config(tmp_path)
    storage = ScanStorage(cfg.sqlite_path)
    market_data = market_data or FakeMarketData()
    scanner = OptionsScanner(cfg, market_data, storage, DiscordAlerter(cfg, storage))
    return scanner, storage


def candidate_payload(**overrides) -> dict:
    data = {
        "ticker": "SPY",
        "pattern": "2-2 reversal",
        "price": 101.0,
        "vwap": 100.0,
        "ema20": 99.0,
        "volume_ratio": 1.3,
        "iv_rank": 25.0,
        "option_type": "CALL",
        "strike": 505,
        "expiry": "2099-01-16",
        "option_mark": 2.10,
        "option_bid": 2.05,
        "option_ask": 2.15,
        "open_interest": 1500,
        "stop": 99.0,
        "target": 104.0,
        "risk_cap": 210.0,
    }
    data.update(overrides)
    return data


def scan(scanner: OptionsScanner, context: dict | None, now: datetime = OPEN_TIME):
    return asyncio.run(
        scanner.scan_ticker("SPY", source="webhook", context=context, now=now)
    )


def test_ordinary_scan_does_not_open_a_position(tmp_path):
    scanner, storage = make_scanner(tmp_path)
    outcome = scan(scanner, {"pattern": "2-2", "price": 101.0, "vwap": 100.0, "ema20": 99.0})
    assert outcome.shadow_id == 0
    assert outcome.shadow_reason.startswith("not_a_candidate:")
    assert storage.shadow_summary().total == 0
    # The scan row itself is still recorded.
    assert storage.latest(limit=1)[0].ticker == "SPY"


def test_provider_failure_does_not_open_a_position(tmp_path):
    scanner, storage = make_scanner(tmp_path, FakeMarketData(price=None, error="timeout"))
    outcome = scan(scanner, None)
    assert outcome.result.score == 0
    assert outcome.result.direction == "UNKNOWN"
    assert outcome.shadow_id == 0
    assert outcome.shadow_reason == "provider_error:timeout"
    assert storage.shadow_summary().open == 0
    # No contract recommendation surfaces anywhere on a failed scan.
    latest = storage.latest(limit=1)[0]
    assert latest.raw.get("contract") is None
    assert latest.raw.get("option_mark") is None


def test_provider_failure_with_full_external_snapshot_still_opens(tmp_path):
    scanner, storage = make_scanner(tmp_path, FakeMarketData(price=None, error="timeout"))
    outcome = scan(scanner, candidate_payload())
    assert outcome.shadow_id > 0
    assert storage.shadow_summary().open == 1


def test_formed_candidate_opens_with_required_fields(tmp_path):
    scanner, storage = make_scanner(tmp_path)
    outcome = scan(scanner, candidate_payload())
    assert outcome.shadow_id > 0
    setup = storage.get_shadow_setup(outcome.shadow_id)
    assert setup.status == "OPEN"
    contract = setup.selected_contract
    assert contract["contract_key"] == "SPY:2099-01-16:CALL:505"
    assert contract["entry_quote"] == 2.10
    assert contract["entry_bid"] == 2.05
    assert contract["entry_ask"] == 2.15
    assert contract["liquidity"] == 1500
    assert contract["stop"] == 99.0
    assert contract["target"] == 104.0
    assert contract["risk_cap"] == 210.0
    assert contract["expiry"] == "2099-01-16"
    assert contract["source_timestamp"]


def test_duplicate_open_candidate_is_suppressed(tmp_path):
    scanner, storage = make_scanner(tmp_path)
    first = scan(scanner, candidate_payload())
    second = scan(scanner, candidate_payload())
    assert first.shadow_id > 0
    assert second.shadow_id == 0
    assert second.shadow_reason == f"duplicate_open:{first.shadow_id}"
    assert storage.shadow_summary().open == 1
    # A different contract is a different candidate.
    third = scan(scanner, candidate_payload(strike=510))
    assert third.shadow_id > 0
    assert storage.shadow_summary().open == 2


def test_stop_resolution_marks_loss(tmp_path):
    market = FakeMarketData(price=101.0)
    scanner, storage = make_scanner(tmp_path, market)
    outcome = scan(scanner, candidate_payload())
    market.price = 98.5  # below the 99.0 stop for a LONG
    counts = asyncio.run(scanner.resolve_open_candidates(now=OPEN_TIME + timedelta(hours=1)))
    assert counts == {"checked": 1, "resolved": 1}
    setup = storage.get_shadow_setup(outcome.shadow_id)
    assert setup.status == "LOSS"
    assert setup.outcome["closed_reason"] == "stop_hit"


def test_target_resolution_marks_win(tmp_path):
    market = FakeMarketData(price=101.0)
    scanner, storage = make_scanner(tmp_path, market)
    outcome = scan(scanner, candidate_payload())
    market.price = 104.5
    asyncio.run(scanner.resolve_open_candidates(now=OPEN_TIME + timedelta(hours=1)))
    setup = storage.get_shadow_setup(outcome.shadow_id)
    assert setup.status == "WIN"
    assert setup.outcome["closed_reason"] == "target_hit"


def test_expiration_resolves_even_without_quotes(tmp_path):
    market = FakeMarketData(price=101.0)
    scanner, storage = make_scanner(tmp_path, market)
    outcome = scan(scanner, candidate_payload(expiry="2026-08-05"))
    market.error = "timeout"
    market.price = None
    asyncio.run(scanner.resolve_open_candidates(now=OPEN_TIME + timedelta(days=2)))
    setup = storage.get_shadow_setup(outcome.shadow_id)
    assert setup.status == "EXPIRED"
    assert setup.outcome["closed_reason"] == "expired"


def test_provider_failure_leaves_candidate_open(tmp_path):
    market = FakeMarketData(price=101.0)
    scanner, storage = make_scanner(tmp_path, market)
    outcome = scan(scanner, candidate_payload())
    market.error = "timeout"
    market.price = None
    counts = asyncio.run(scanner.resolve_open_candidates(now=OPEN_TIME + timedelta(hours=1)))
    assert counts == {"checked": 1, "resolved": 0}
    assert storage.get_shadow_setup(outcome.shadow_id).status == "OPEN"


def test_cancellation_is_a_supported_resolution(tmp_path):
    scanner, storage = make_scanner(tmp_path)
    outcome = scan(scanner, candidate_payload())
    updated = storage.update_shadow_outcome(
        outcome.shadow_id, status="CANCELLED", outcome={"closed_reason": "operator_cancelled"}
    )
    assert updated.status == "CANCELLED"
    assert storage.shadow_summary().cancelled == 1


def test_restart_recovery_resolves_preexisting_open_rows(tmp_path):
    market = FakeMarketData(price=101.0)
    scanner, _storage = make_scanner(tmp_path, market)
    outcome = scan(scanner, candidate_payload())

    # Simulate a service restart: fresh storage + scanner over the same file.
    cfg = make_config(tmp_path)
    storage2 = ScanStorage(cfg.sqlite_path)
    market2 = FakeMarketData(price=104.5)
    scanner2 = OptionsScanner(cfg, market2, storage2, DiscordAlerter(cfg, storage2))
    # Reconciliation must not touch fully-formed OPEN candidates.
    assert storage2.reconcile_open_non_candidates(classify_candidate) == 0
    asyncio.run(scanner2.resolve_open_candidates(now=OPEN_TIME + timedelta(hours=2)))
    assert storage2.get_shadow_setup(outcome.shadow_id).status == "WIN"


def test_append_only_reconciliation_of_legacy_open_rows(tmp_path):
    scanner, storage = make_scanner(tmp_path)
    # Legacy behavior: every scan (even a failed one) became an OPEN row.
    legacy = ScoreResult("SPY", "UNKNOWN", 0, "N/A", {}, {"market_data_error": "HTTPStatusError"})
    legacy_id = storage.record_shadow_setup(
        legacy,
        scan_id=1,
        setup_inputs={"ticker": "SPY", "market_data_error": "HTTPStatusError"},
        provider_snapshot={"provider": "public", "error": "HTTPStatusError"},
        timestamp=OPEN_TIME,
    )
    assert storage.get_shadow_setup(legacy_id).status == "OPEN"

    reconciled = storage.reconcile_open_non_candidates(classify_candidate)
    assert reconciled == 1
    setup = storage.get_shadow_setup(legacy_id)
    assert setup.status == "REJECTED"
    assert setup.outcome["closed_reason"] == "reconciled_non_candidate"
    # Row data preserved verbatim (append-only classification).
    assert setup.setup_inputs == {"ticker": "SPY", "market_data_error": "HTTPStatusError"}
    assert storage.shadow_summary().rejected == 1
    # Idempotent.
    assert storage.reconcile_open_non_candidates(classify_candidate) == 0


def test_reconciliation_spares_open_candidates_mixed_with_legacy(tmp_path):
    scanner, storage = make_scanner(tmp_path)
    good = scan(scanner, candidate_payload())
    legacy = ScoreResult("QQQ", "UNKNOWN", 0, "N/A", {}, {})
    storage.record_shadow_setup(
        legacy, scan_id=2, setup_inputs={"ticker": "QQQ"}, provider_snapshot={}, timestamp=OPEN_TIME
    )
    assert storage.reconcile_open_non_candidates(classify_candidate) == 1
    assert storage.get_shadow_setup(good.shadow_id).status == "OPEN"


def test_failed_scan_never_sends_candidate_alert(tmp_path, monkeypatch):
    market = FakeMarketData(price=None, error="timeout")
    scanner, _storage = make_scanner(tmp_path, market)
    object.__setattr__(scanner.config, "discord_webhook_url", "https://discord.test/webhook")
    sent = []

    async def record_alert(ticker, normalized, now):
        sent.append(ticker)

    monkeypatch.setattr(scanner, "_maybe_send_candidate_alert", record_alert)
    scan(scanner, {"signa_grade": "A", "signa_score": 85, "signa_daily_direction": "UP"})
    assert sent == []


def test_direct_resolution_rules_are_deterministic():
    contract = {"stop": 99.0, "target": 104.0, "expiry": "2099-01-16"}
    now = OPEN_TIME
    assert resolve_open_setup(
        direction="LONG", contract=contract, underlying_price=100.0, now=now
    ) is None
    status, _ = resolve_open_setup(
        direction="SHORT", contract={"stop": 104.0, "target": 99.0}, underlying_price=98.0, now=now
    )
    assert status == "WIN"


def test_storage_connection_context_closes_database(tmp_path):
    storage = ScanStorage(tmp_path / "scanner.sqlite")

    with storage._connect() as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1

    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        conn.execute("SELECT 1")
