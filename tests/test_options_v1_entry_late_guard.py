"""Late-entry guard for the OPTIONS_PAPER_V1 ACTIVE lane (2026-09-14).

The Daily lane evaluates its trigger at the 5-minute scan, so a daily setup
that has already run was entered at market with its own target behind it and
"hit" on the next snapshot. Rows 9170 (AAPL) and 9171 (SPY) on 2026-09-11 are
the recorded cases. The guard refuses the ACTIVE paper entry when the live
price is already past the target, past the stop, or leaves less than the
configured reward per unit of risk. Everything else -- setup verdict, targets,
risk sizing, the counterfactual lane -- is untouched.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from alert_ranker.config import ScannerConfig, load_config
from alert_ranker.discord import DiscordAlerter
from alert_ranker.market_data import OptionChain, OptionContractQuote
from alert_ranker.paper_v1 import (
    DEFAULT_MIN_REMAINING_RR,
    ENTRY_LATE_STATUS,
    POLICY_ID,
    entry_late_reason,
    remaining_reward_to_risk,
)
from alert_ranker.scanner import OptionsScanner
from alert_ranker.storage import ScanStorage
from alert_ranker.tastytrade_client import MarketSnapshot

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 11, 11, 55, tzinfo=NY)
EXPIRY = "2026-10-30"


# --- pure helper -----------------------------------------------------------


def test_remaining_rr_measures_from_live_price_not_trigger():
    # LONG: stop 90, target 110. From 100 the trade has 10 reward / 10 risk.
    assert remaining_reward_to_risk("LONG", 100.0, 90.0, 110.0) == pytest.approx(1.0)
    # From 105 only 5 reward remains against 15 risk.
    assert remaining_reward_to_risk("LONG", 105.0, 90.0, 110.0) == pytest.approx(5 / 15)
    # SHORT mirror.
    assert remaining_reward_to_risk("SHORT", 100.0, 110.0, 90.0) == pytest.approx(1.0)
    assert remaining_reward_to_risk("SHORT", 95.0, 110.0, 90.0) == pytest.approx(5 / 15)


def test_remaining_rr_is_undefined_at_or_through_stop_or_with_missing_inputs():
    assert remaining_reward_to_risk("LONG", 90.0, 90.0, 110.0) is None
    assert remaining_reward_to_risk("LONG", 85.0, 90.0, 110.0) is None
    assert remaining_reward_to_risk("LONG", None, 90.0, 110.0) is None
    assert remaining_reward_to_risk("LONG", 100.0, None, 110.0) is None
    assert remaining_reward_to_risk("LONG", 100.0, 90.0, None) is None
    assert remaining_reward_to_risk("SIDEWAYS", 100.0, 90.0, 110.0) is None


def test_entry_late_reason_orders_target_then_stop_then_ratio():
    assert entry_late_reason("LONG", 111.0, 90.0, 110.0) == "price_past_target"
    assert entry_late_reason("LONG", 110.0, 90.0, 110.0) == "price_past_target"
    assert entry_late_reason("LONG", 89.0, 90.0, 110.0) == "price_past_stop"
    assert entry_late_reason("LONG", 90.0, 90.0, 110.0) == "price_past_stop"
    assert entry_late_reason("LONG", 105.0, 90.0, 110.0) == "remaining_rr_0.33_below_1.00"
    assert entry_late_reason("LONG", 100.0, 90.0, 110.0) is None
    assert entry_late_reason("LONG", 99.0, 90.0, 110.0) is None
    # SHORT mirror.
    assert entry_late_reason("SHORT", 89.0, 110.0, 90.0) == "price_past_target"
    assert entry_late_reason("SHORT", 111.0, 110.0, 90.0) == "price_past_stop"
    assert entry_late_reason("SHORT", 95.0, 110.0, 90.0) == "remaining_rr_0.33_below_1.00"
    assert entry_late_reason("SHORT", 100.0, 110.0, 90.0) is None


def test_entry_late_reason_never_invents_a_failure_for_missing_inputs():
    # These already fail closed downstream (target_missing / invalidation
    # missing / contract selection); the guard must stay out of the way.
    assert entry_late_reason("LONG", None, 90.0, 110.0) is None
    assert entry_late_reason("LONG", 100.0, None, 110.0) is None
    assert entry_late_reason("LONG", 100.0, 90.0, None) is None
    assert entry_late_reason("", 100.0, 90.0, 110.0) is None


def test_min_rr_zero_keeps_only_the_hard_target_and_stop_checks():
    assert entry_late_reason("LONG", 109.0, 90.0, 110.0, min_rr=0.0) is None
    assert entry_late_reason("LONG", 110.0, 90.0, 110.0, min_rr=0.0) == "price_past_target"
    assert entry_late_reason("LONG", 90.0, 90.0, 110.0, min_rr=0.0) == "price_past_stop"


def test_config_default_and_env_override(monkeypatch):
    assert DEFAULT_MIN_REMAINING_RR == 1.0
    monkeypatch.delenv("OPTIONS_PAPER_V1_MIN_REMAINING_RR", raising=False)
    assert load_config().paper_v1_min_remaining_rr == 1.0
    monkeypatch.setenv("OPTIONS_PAPER_V1_MIN_REMAINING_RR", "1.5")
    assert load_config().paper_v1_min_remaining_rr == 1.5


# --- the two recorded rows replayed through the real scanner ---------------


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
        watchlist=["AAPL", "SPY"],
        interval_minutes=5,
        sqlite_path=tmp_path / "options_scanner.sqlite",
        public_account_id="ACC12345",
        **overrides,
    )


class _Market:
    """Chain double that records whether the scanner ever asked for a chain."""

    provider_name = "public"
    last_error = None

    def __init__(self, price: float, symbol: str, strike: float, bid: float, ask: float):
        self.price = price
        self.contract = OptionContractQuote(
            symbol=symbol,
            option_type="CALL",
            strike=strike,
            bid=bid,
            ask=ask,
            mid=round((bid + ask) / 2.0, 4),
            last=ask,
            volume=500,
            open_interest=5000,
            delta=0.38,
            implied_volatility=0.30,
        )
        self.chain_calls = 0

    async def fetch_market_snapshot(self, ticker: str) -> MarketSnapshot:
        return MarketSnapshot(ticker.upper(), price=self.price, volume=10_000_000)

    async def fetch_option_expirations(self, ticker: str) -> list[str]:
        self.chain_calls += 1
        return [EXPIRY]

    async def fetch_option_chain(self, ticker: str, expiration: str | None = None) -> OptionChain:
        self.chain_calls += 1
        return OptionChain(ticker.upper(), expiration, calls=(self.contract,), puts=())


def _context(*, trigger: float, stop: float, target_1: float, target_2: float, setup_type: str):
    # Field set taken from the recorded setup_inputs_json of rows 9170/9171
    # (status TRIGGERED, direction CALL, proof VALID, market VALID).
    return {
        "pattern": "2-2-2",
        "direction": "LONG",
        "setup_status": "TRIGGERED",
        "setup_direction": "CALL",
        "setup_type": setup_type,
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


ROW_9170 = dict(
    ticker="AAPL",
    price=334.28,
    context=_context(
        trigger=326.74, stop=316.51, target_1=328.40, target_2=328.93,
        setup_type="DAILY_222_CONTINUATION",
    ),
    market=("AAPL261030C00350000", 350.0, 7.00, 7.70),
)
ROW_9171 = dict(
    ticker="SPY",
    price=766.01,
    context=_context(
        trigger=760.11, stop=756.64, target_1=760.94, target_2=761.73,
        setup_type="DAILY_222_REVERSAL",
    ),
    market=("SPY261030C00780000", 780.0, 8.79, 8.84),
)


def _run(tmp_path: Path, row: dict, price: float | None = None, **cfg_overrides):
    config = _cfg(tmp_path, **cfg_overrides)
    storage = ScanStorage(config.sqlite_path)
    symbol, strike, bid, ask = row["market"]
    market = _Market(price if price is not None else row["price"], symbol, strike, bid, ask)
    scanner = OptionsScanner(config, market, storage, DiscordAlerter(config, storage))
    outcome = asyncio.run(
        scanner.scan_ticker(row["ticker"], source="scheduled", context=dict(row["context"]), now=NOW)
    )
    return outcome, storage, market


@pytest.mark.parametrize("row", [ROW_9170, ROW_9171], ids=["9170_AAPL", "9171_SPY"])
def test_recorded_late_entries_are_refused_before_any_chain_call(tmp_path, row):
    outcome, storage, market = _run(tmp_path, row)
    raw = outcome.result.raw

    # The setup verdict itself is untouched: still TRIGGERED, still proven.
    assert raw["setup_status"] == "TRIGGERED"
    assert raw["setup_proof_status"] == "VALID"
    assert raw["target_1"] == row["context"]["target_1"]
    assert raw["stop"] == row["context"]["stop"]

    # The ACTIVE paper entry is refused, with the reason on the row.
    assert raw["paper_policy_id"] == POLICY_ID
    assert raw["paper_policy_status"] == ENTRY_LATE_STATUS
    assert raw["paper_policy_reason"] == "price_past_target"
    assert raw["paper_entry_remaining_rr"] < 0

    # No option chain was fetched, no contract selected, no risk consumed.
    assert market.chain_calls == 0
    assert "contract" not in raw or raw.get("contract") in (None, "")
    assert raw.get("planned_risk_dollars") in (None, 0, 0.0)

    # No alert, no shadow row, and the suppression reason names the guard.
    assert outcome.alert_sent is False
    assert outcome.shadow_id == 0
    assert outcome.alert_suppression_reason == "ENTRY_LATE:price_past_target"
    assert storage.open_setups_after(0) == []


@pytest.mark.parametrize(
    "row, open_price",
    [(ROW_9170, 322.0), (ROW_9171, 758.5)],
    ids=["9170_AAPL", "9171_SPY"],
)
def test_same_setups_open_normally_when_price_still_offers_the_reward(tmp_path, row, open_price):
    # Same levels, live price early enough that >= 1.0 reward per unit of
    # risk remains (AAPL 6.40/5.49 = 1.17, SPY 2.44/1.86 = 1.31).
    outcome, storage, market = _run(tmp_path, row, price=open_price)
    raw = outcome.result.raw
    assert raw["paper_policy_status"] == "VALID", raw.get("paper_policy_reason")
    assert raw["paper_entry_remaining_rr"] == pytest.approx(
        remaining_reward_to_risk("LONG", open_price, row["context"]["stop"], row["context"]["target_1"])
    )
    assert raw["paper_entry_remaining_rr"] >= 1.0
    assert market.chain_calls > 0
    assert outcome.shadow_id > 0
    assert outcome.alert_sent is False  # Discord stays fail-closed regardless
    stored = storage.get_shadow_setup(outcome.shadow_id)
    assert stored.selected_contract["paper_policy_status"] == "VALID"
    assert stored.selected_contract["paper_entry_remaining_rr"] == pytest.approx(
        raw["paper_entry_remaining_rr"]
    )


@pytest.mark.parametrize(
    "row, expected",
    [(ROW_9170, "remaining_rr_0.16_below_1.00"), (ROW_9171, "remaining_rr_0.24_below_1.00")],
    ids=["9170_AAPL", "9171_SPY"],
)
def test_recorded_rows_fail_the_ratio_floor_even_at_their_own_trigger(tmp_path, row, expected):
    # Both recorded setups offered under 0.25 reward per unit of risk at the
    # trigger itself (setup_rr_1 0.16 / 0.24 in the journal). With price at
    # the trigger the guard refuses on the ratio, not on the target.
    outcome, _, market = _run(tmp_path, row, price=row["context"]["setup_entry_trigger"])
    raw = outcome.result.raw
    assert raw["paper_policy_status"] == ENTRY_LATE_STATUS
    assert raw["paper_policy_reason"] == expected
    assert market.chain_calls == 0


def test_ratio_floor_is_operator_configurable(tmp_path):
    # Same AAPL setup at its trigger opens once the floor is lowered to 0.
    outcome, _, market = _run(tmp_path, ROW_9170, price=326.74, paper_v1_min_remaining_rr=0.0)
    assert outcome.result.raw["paper_policy_status"] == "VALID"
    assert market.chain_calls > 0


def test_counterfactual_lane_is_not_gated_by_the_guard(tmp_path):
    """Counterfactual rows exist to keep the rejected population observable.

    The guard lives only on the ACTIVE path (base ``_apply_paper_v1_contract``).
    The hardened scanner's counterfactual branch never calls it, so a
    counterfactual observer row with price already past target is still
    priced and recorded exactly as before.
    """
    from alert_ranker.v1_evidence_hardening import build_v1_evidence_hardening

    config = _cfg(tmp_path)
    storage = ScanStorage(config.sqlite_path)
    symbol, strike, bid, ask = ROW_9170["market"]
    market = _Market(ROW_9170["price"], symbol, strike, bid, ask)
    scanner_cls = build_v1_evidence_hardening(OptionsScanner)
    scanner = scanner_cls(config, market, storage, DiscordAlerter(config, storage))

    normalized = {
        **ROW_9170["context"],
        "ticker": "AAPL",
        "price": ROW_9170["price"],
        "counterfactual_observer": True,
    }
    data = asyncio.run(scanner._apply_paper_v1_contract("AAPL", normalized, "LONG", NOW))
    assert data["paper_evidence_lane"] == "COUNTERFACTUAL"
    assert data["paper_policy_status"] == "VALID"
    assert data["risk_budget_consumed"] is False
    assert market.chain_calls > 0

    # And the ACTIVE path on the very same hardened scanner still refuses it.
    market.chain_calls = 0
    active = dict(normalized)
    active.pop("counterfactual_observer")
    data = asyncio.run(scanner._apply_paper_v1_contract("AAPL", active, "LONG", NOW))
    assert data["paper_policy_status"] == ENTRY_LATE_STATUS
    assert data["paper_policy_reason"] == "price_past_target"
    assert "paper_evidence_lane" not in data
    assert market.chain_calls == 0
