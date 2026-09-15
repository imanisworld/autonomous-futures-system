"""ENTRY_LATE episode block (operator ruling 2026-09-14).

The late-entry guard (#570) was per-scan. A daily episode refused ENTRY_LATE
at 10:20 could therefore open ACTIVE at 13:45 if price drifted back into a
favourable reward/risk zone -- a stale entry with no new mechanical trigger,
exactly the hidden entry state the guard was meant to remove.

Ruling:
1. Once an episode's first actionable ACTIVE entry is refused ENTRY_LATE, that
   episode can never become ACTIVE.
2. A genuinely new mechanical trigger (new trigger level or new episode
   bucket) is a new episode and is unaffected.
3. The refused episode is preserved as COUNTERFACTUAL evidence (priced from
   the real chain, no ACTIVE risk, no alert) so the filter stays measurable.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from alert_ranker.config import ScannerConfig
from alert_ranker.discord import DiscordAlerter
from alert_ranker.market_data import OptionChain, OptionContractQuote
from alert_ranker.paper_v1 import ENTRY_LATE_STATUS, EPISODE_BLOCKED_REASON, POLICY_ID
from alert_ranker.scanner import OptionsScanner
from alert_ranker.storage import ScanStorage
from alert_ranker.tastytrade_client import MarketSnapshot

NY = ZoneInfo("America/New_York")
T0 = datetime(2026, 9, 14, 10, 20, tzinfo=NY)
EXPIRY = "2026-10-30"


def _cfg(tmp_path: Path, **overrides) -> ScannerConfig:
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
        **overrides,
    )


class _Market:
    provider_name = "public"
    last_error = None

    def __init__(self, price: float, *, liquid: bool = True):
        self.price = price
        self.liquid = liquid
        self.chain_calls = 0

    async def fetch_market_snapshot(self, ticker: str) -> MarketSnapshot:
        return MarketSnapshot(ticker.upper(), price=self.price, volume=10_000_000)

    async def fetch_option_expirations(self, ticker: str) -> list[str]:
        self.chain_calls += 1
        return [EXPIRY]

    async def fetch_option_chain(self, ticker: str, expiration: str | None = None) -> OptionChain:
        self.chain_calls += 1
        if self.liquid:
            contract = OptionContractQuote(
                symbol="SPY261030C00780000", option_type="CALL", strike=780.0,
                bid=8.79, ask=8.84, mid=8.815, last=8.84, volume=500, open_interest=5000,
                delta=0.39, implied_volatility=0.30,
            )
        else:
            # Wide, empty book: fails the liquidity checks -> no_liquid_contract.
            contract = OptionContractQuote(
                symbol="SPY261030C00780000", option_type="CALL", strike=780.0,
                bid=1.00, ask=9.00, mid=5.0, last=5.0, volume=0, open_interest=0,
                delta=0.39, implied_volatility=0.30,
            )
        return OptionChain(ticker.upper(), expiration, calls=(contract,), puts=())


def _context(trigger=760.11, stop=756.64, target_1=764.47, target_2=765.14):
    # SPY 2026-09-11 DAILY_222_REVERSAL LONG, with the 1R-floor targets.
    return {
        "pattern": "2-2-2",
        "direction": "LONG",
        "setup_status": "TRIGGERED",
        "setup_direction": "CALL",
        "setup_type": "DAILY_222_REVERSAL",
        "setup_timeframe": "1D",
        "setup_entry_trigger": trigger,
        "underlying_invalidation": stop,
        "stop": stop,
        "target": target_1,
        "target_1": target_1,
        "target_2": target_2,
        "setup_proof_status": "VALID",
        "setup_market_status": "VALID",
        "volume_ratio": 1.4,
        "iv_rank": 30.0,
    }


def _scanner(tmp_path: Path, market: _Market, storage: ScanStorage | None = None):
    config = _cfg(tmp_path)
    storage = storage or ScanStorage(config.sqlite_path)
    return OptionsScanner(config, market, storage, DiscordAlerter(config, storage)), storage


def _scan(scanner, market: _Market, *, price: float, now: datetime, context=None):
    market.price = price
    return asyncio.run(
        scanner.scan_ticker("SPY", source="scheduled", context=dict(context or _context()), now=now)
    )


def _active_rows(storage: ScanStorage) -> list:
    """Every journal row in the ACTIVE lane, whatever its status."""
    import json
    import sqlite3

    # Lane is read the way v1_diagnostics reads it: selected contract first,
    # then the setup inputs (ACTIVE rows carry it there).
    with sqlite3.connect(storage.path) as conn:
        rows = conn.execute(
            "SELECT id, status, selected_contract_json, setup_inputs_json "
            "FROM options_shadow_journal ORDER BY id"
        ).fetchall()
    out = []
    for row_id, status, selected, inputs in rows:
        lane = json.loads(selected).get("paper_evidence_lane") or json.loads(inputs).get(
            "paper_evidence_lane"
        )
        if lane == "ACTIVE":
            out.append((row_id, status))
    return out


def test_first_entry_late_blocks_the_episode_and_keeps_it_as_counterfactual(tmp_path):
    market = _Market(price=766.01)  # past the 764.47 target at first sight
    scanner, storage = _scanner(tmp_path, market)

    first = _scan(scanner, market, price=766.01, now=T0)
    raw = first.result.raw
    assert raw["entry_late_reason"] == "price_past_target"
    assert raw["paper_evidence_lane"] == "COUNTERFACTUAL"
    assert raw["risk_budget_consumed"] is False
    assert first.alert_sent is False
    assert first.shadow_id > 0
    stored = storage.get_shadow_setup(first.shadow_id)
    assert stored.selected_contract["paper_evidence_lane"] == "COUNTERFACTUAL"
    assert stored.selected_contract["counterfactual_filter_reason"] == "ENTRY_LATE:price_past_target"
    active_key = stored.selected_contract["entry_late_active_episode_key"]
    assert active_key == "SPY|ACTIVE|1D|DAILY_222_REVERSAL|LONG|760.1100@1D:2026-09-14"
    assert storage.episode_block("SPY", active_key) == "ENTRY_LATE:price_past_target"
    assert _active_rows(storage) == []


def test_same_episode_cannot_open_later_when_price_comes_back(tmp_path):
    market = _Market(price=766.01)
    scanner, storage = _scanner(tmp_path, market)
    _scan(scanner, market, price=766.01, now=T0)
    market.chain_calls = 0

    # 13:45: price has pulled back to the trigger -- 1.26R now remains. The
    # old per-scan guard would have opened this. The episode is blocked.
    later = _scan(scanner, market, price=760.11, now=T0 + timedelta(hours=3, minutes=25))
    raw = later.result.raw
    assert raw["paper_policy_id"] == POLICY_ID
    assert raw["paper_policy_status"] == ENTRY_LATE_STATUS
    assert raw["paper_policy_reason"] == EPISODE_BLOCKED_REASON
    assert raw["blocked_active_episode_key"].endswith("@1D:2026-09-14")
    assert later.alert_sent is False
    assert later.alert_suppression_reason == f"ENTRY_LATE:{EPISODE_BLOCKED_REASON}"
    assert later.shadow_id == 0
    assert market.chain_calls == 0  # no chain, no contract, no risk, nothing
    assert _active_rows(storage) == []

    # And it stays blocked on every later scan that day.
    again = _scan(scanner, market, price=761.0, now=T0 + timedelta(hours=5))
    assert again.result.raw["paper_policy_reason"] == EPISODE_BLOCKED_REASON
    assert again.shadow_id == 0
    assert market.chain_calls == 0


def test_a_new_mechanical_trigger_is_a_new_episode_and_may_open(tmp_path):
    market = _Market(price=766.01)
    scanner, storage = _scanner(tmp_path, market)
    _scan(scanner, market, price=766.01, now=T0)

    # A different trigger level (a new setup) at a favourable price opens.
    fresh = _context(trigger=770.0, stop=766.5, target_1=774.03, target_2=775.3)
    opened = _scan(scanner, market, price=770.2, now=T0 + timedelta(hours=1), context=fresh)
    assert opened.result.raw["paper_policy_status"] == "VALID"
    assert opened.result.raw["paper_evidence_lane"] == "ACTIVE"
    assert opened.shadow_id > 0
    assert storage.get_shadow_setup(opened.shadow_id).selected_contract["episode_key"] == (
        "SPY|ACTIVE|1D|DAILY_222_REVERSAL|LONG|770.0000@1D:2026-09-14"
    )
    assert len(_active_rows(storage)) == 1


def test_next_session_is_a_new_episode_bucket(tmp_path):
    market = _Market(price=766.01)
    scanner, storage = _scanner(tmp_path, market)
    _scan(scanner, market, price=766.01, now=T0)

    # Same trigger level the next trading day = new 1D bucket = new episode.
    next_day = _scan(scanner, market, price=760.5, now=T0 + timedelta(days=1))
    assert next_day.result.raw["paper_policy_status"] == "VALID"
    assert next_day.result.raw["paper_evidence_lane"] == "ACTIVE"
    assert next_day.shadow_id > 0


def test_block_holds_even_when_the_counterfactual_could_not_be_priced(tmp_path):
    # Illiquid chain: the counterfactual row cannot be journalled, but the
    # block must still be recorded -- otherwise the episode could open later.
    market = _Market(price=766.01, liquid=False)
    scanner, storage = _scanner(tmp_path, market)
    first = _scan(scanner, market, price=766.01, now=T0)
    assert first.result.raw["entry_late_reason"] == "price_past_target"
    assert first.result.raw["paper_policy_status"] == "DATA_INVALID"
    assert first.result.raw["paper_policy_reason"].startswith("no_liquid_contract")
    assert first.shadow_id == 0
    assert storage.episode_block(
        "SPY", "SPY|ACTIVE|1D|DAILY_222_REVERSAL|LONG|760.1100@1D:2026-09-14"
    ) == "ENTRY_LATE:price_past_target"

    market.liquid = True
    market.chain_calls = 0
    later = _scan(scanner, market, price=760.11, now=T0 + timedelta(hours=3))
    assert later.result.raw["paper_policy_reason"] == EPISODE_BLOCKED_REASON
    assert later.shadow_id == 0
    assert market.chain_calls == 0


def test_block_survives_a_scanner_restart(tmp_path):
    market = _Market(price=766.01)
    scanner, storage = _scanner(tmp_path, market)
    _scan(scanner, market, price=766.01, now=T0)

    # New process, same sqlite file.
    scanner2, storage2 = _scanner(tmp_path, market, ScanStorage(storage.path))
    market.chain_calls = 0
    later = _scan(scanner2, market, price=760.11, now=T0 + timedelta(hours=2))
    assert later.result.raw["paper_policy_reason"] == EPISODE_BLOCKED_REASON
    assert later.shadow_id == 0
    assert market.chain_calls == 0


def test_counterfactual_observer_rows_never_create_blocks(tmp_path):
    market = _Market(price=766.01)
    scanner, storage = _scanner(tmp_path, market)
    observer = {**_context(), "counterfactual_observer": True, "setup_status": "OBSERVE"}
    outcome = _scan(scanner, market, price=766.01, now=T0, context=observer)
    assert outcome.alert_sent is False
    assert storage.episode_block(
        "SPY", "SPY|ACTIVE|1D|DAILY_222_REVERSAL|LONG|760.1100@1D:2026-09-14"
    ) is None

    # The ACTIVE lane is therefore still free to open this episode at a
    # favourable price (no ENTRY_LATE ever happened on the ACTIVE path).
    opened = _scan(scanner, market, price=760.11, now=T0 + timedelta(minutes=5))
    assert opened.result.raw["paper_policy_status"] == "VALID"
    assert opened.result.raw["paper_evidence_lane"] == "ACTIVE"


def test_observer_rows_are_gated_from_discord_even_with_the_causal_lane_off(tmp_path):
    # Latent exposure closed alongside the ruling: with bar_context disabled
    # an OBSERVE row used to reach Discord eligibility and the ACTIVE journal
    # path. Now it is gated on its own flag.
    market = _Market(price=766.01)
    scanner, storage = _scanner(tmp_path, market)
    assert scanner._causal_lane_active() is False
    outcome = _scan(scanner, market, price=766.01, now=T0)
    assert outcome.alert_suppression_reason == "entry_late_counterfactual_only:price_past_target"
    gate = scanner._structural_gate({"counterfactual_observer": True})
    assert gate == "counterfactual_observer_only"
