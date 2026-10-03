"""Universe expansion: coverage only — no rule / threshold changes."""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from alert_ranker.config import ScannerConfig, load_config
from alert_ranker.paper_v1 import (
    MAX_ABS_DELTA,
    MAX_AGGREGATE_OPEN_RISK_DOLLARS,
    MAX_SPREAD_PERCENT,
    MAX_TRADE_RISK_DOLLARS,
    MIN_ABS_DELTA,
    MIN_OPEN_INTEREST,
    MIN_OPTION_VOLUME,
    choose_contract,
)
from alert_ranker.scanner import OptionsScanner
from alert_ranker.storage import ScanStorage
from alert_ranker.universe import (
    CONDITIONAL,
    CORE,
    DEFAULT_WATCHLIST,
    EXPANDED,
    default_watchlist_csv,
    resolve_watchlist,
    watchlist_tier,
)


# Frozen V1 thresholds — universe expansion must not touch these.
_FROZEN_GATES = {
    "MAX_SPREAD_PERCENT": 10.0,
    "MIN_OPTION_VOLUME": 100,
    "MIN_OPEN_INTEREST": 500,
    "MIN_ABS_DELTA": 0.30,
    "MAX_ABS_DELTA": 0.70,
    "MAX_TRADE_RISK_DOLLARS": 300.0,
    "MAX_AGGREGATE_OPEN_RISK_DOLLARS": 1000.0,
}


def test_default_watchlist_includes_requested_symbols_in_tier_order():
    watchlist = list(DEFAULT_WATCHLIST)
    assert len(watchlist) == len(set(watchlist))
    assert len(watchlist) == len(CORE) + len(EXPANDED) + len(CONDITIONAL)

    for symbol in CORE:
        assert symbol in watchlist
        assert watchlist_tier(symbol) == "core"
    for symbol in EXPANDED:
        assert symbol in watchlist
        assert watchlist_tier(symbol) == "expanded"
    for symbol in CONDITIONAL:
        assert symbol in watchlist
        assert watchlist_tier(symbol) == "conditional"

    # CORE before EXPANDED before CONDITIONAL (scan order = list order).
    assert watchlist.index("SPY") < watchlist.index("ORCL") < watchlist.index("KWEB")
    assert watchlist[: len(CORE)] == list(CORE)
    assert watchlist[len(CORE) : len(CORE) + len(EXPANDED)] == list(EXPANDED)
    assert watchlist[-len(CONDITIONAL) :] == list(CONDITIONAL)


def test_load_config_default_uses_canonical_universe():
    cfg = load_config(environ=[])
    assert cfg.watchlist == list(DEFAULT_WATCHLIST)
    assert "META" in cfg.watchlist
    assert "IBIT" in cfg.watchlist
    assert "DIA" in cfg.watchlist
    assert "KWEB" in cfg.watchlist
    assert cfg.alert_threshold == 7


def test_env_override_still_respected_and_deduped():
    cfg = load_config(
        environ=[("OPTIONS_SCANNER_WATCHLIST", "spy, meta, SPY, kweb, meta")]
    )
    assert cfg.watchlist == ["SPY", "META", "KWEB"]


def test_resolve_watchlist_empty_falls_back_to_default():
    assert resolve_watchlist(None) == list(DEFAULT_WATCHLIST)
    assert resolve_watchlist("") == list(DEFAULT_WATCHLIST)
    assert resolve_watchlist("  ") == list(DEFAULT_WATCHLIST)
    assert default_watchlist_csv() == ",".join(DEFAULT_WATCHLIST)


def test_kweb_illiquid_contract_still_rejected():
    """CONDITIONAL membership does not bypass V1 liquidity / quality gates."""
    assert "KWEB" in CONDITIONAL
    illiquid = SimpleNamespace(
        symbol="KWEB261120C00040000",
        option_type="CALL",
        strike=40.0,
        bid=0.05,
        ask=1.50,  # wide spread
        mid=0.775,
        volume=1,  # below MIN_OPTION_VOLUME
        open_interest=10,  # below MIN_OPEN_INTEREST
        delta=0.40,
        gamma=None,
        theta=None,
        implied_volatility=0.5,
        quote_timestamp="2026-09-29T14:00:00+00:00",
        source="test",
    )
    decision = choose_contract((illiquid,), option_type="CALL", underlying_price=38.0)
    assert not decision.valid
    assert decision.status == "DATA_INVALID"
    assert "no_liquid_contract" in decision.reason


def test_kweb_wide_spread_alone_still_rejected():
    wide = SimpleNamespace(
        symbol="KWEB261120C00040000",
        option_type="CALL",
        strike=40.0,
        bid=1.00,
        ask=2.00,  # 66%+ spread vs mid
        mid=1.50,
        volume=5_000,
        open_interest=5_000,
        delta=0.40,
        gamma=None,
        theta=None,
        implied_volatility=0.5,
        quote_timestamp="2026-09-29T14:00:00+00:00",
        source="test",
    )
    decision = choose_contract((wide,), option_type="CALL", underlying_price=38.0)
    assert not decision.valid
    assert "no_liquid_contract" in decision.reason


def test_existing_risk_and_liquidity_gates_unchanged():
    assert MAX_SPREAD_PERCENT == _FROZEN_GATES["MAX_SPREAD_PERCENT"]
    assert MIN_OPTION_VOLUME == _FROZEN_GATES["MIN_OPTION_VOLUME"]
    assert MIN_OPEN_INTEREST == _FROZEN_GATES["MIN_OPEN_INTEREST"]
    assert MIN_ABS_DELTA == _FROZEN_GATES["MIN_ABS_DELTA"]
    assert MAX_ABS_DELTA == _FROZEN_GATES["MAX_ABS_DELTA"]
    assert MAX_TRADE_RISK_DOLLARS == _FROZEN_GATES["MAX_TRADE_RISK_DOLLARS"]
    assert MAX_AGGREGATE_OPEN_RISK_DOLLARS == _FROZEN_GATES["MAX_AGGREGATE_OPEN_RISK_DOLLARS"]

    cfg = load_config(environ=[])
    assert cfg.alert_threshold == 7
    assert cfg.paper_v1_min_remaining_rr == 1.0
    assert cfg.paper_v1_daily_min_target_rr == 1.0
    assert cfg.interval_minutes == 5


def test_scan_watchlist_visits_symbols_in_configured_order(tmp_path):
    """No separate batcher: scan order is watchlist order (CORE→EXPANDED→CONDITIONAL)."""
    ordered = ["SPY", "ORCL", "KWEB"]
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
        watchlist=ordered,
        interval_minutes=5,
        sqlite_path=tmp_path / "options_scanner.sqlite",
        public_account_id="ACC12345",
    )
    storage = ScanStorage(cfg.sqlite_path)
    scanner = OptionsScanner(cfg, object(), storage, object())
    seen: list[str] = []

    async def _fake_scan(ticker, *, source, context=None, now=None):
        seen.append(ticker)
        return SimpleNamespace(ticker=ticker)

    scanner.scan_ticker = _fake_scan  # type: ignore[method-assign]
    now = datetime(2026, 9, 29, 10, 30, tzinfo=ZoneInfo("America/New_York"))
    asyncio.run(scanner.scan_watchlist(source="scheduled", now=now))
    assert seen == ordered
    assert watchlist_tier(seen[0]) == "core"
    assert watchlist_tier(seen[1]) == "expanded"
    assert watchlist_tier(seen[2]) == "conditional"


def test_capacity_preflight_fails_closed_without_real_credentials(tmp_path):
    """Real 66-symbol capacity gate must not PASS on stubs or missing providers."""
    import json
    import subprocess
    import sys

    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    out = tmp_path / "capacity.json"
    proc = subprocess.run(
        [
            sys.executable,
            "scripts/options_v1_capacity_preflight.py",
            "--json",
            "--out",
            str(out),
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        env={
            "PATH": __import__("os").environ.get("PATH", ""),
            "PYTHONPATH": str(repo_root),
        },
        check=False,
    )
    assert proc.returncode != 0
    payload = json.loads(out.read_text())
    assert payload["verdict"] == "FAIL"
    assert payload["candidate_count"] == 66
    assert payload["stubs_accepted"] is False
    assert payload["live_watchlist_changed"] is False
    assert payload["deploy_performed"] is False
    assert any("missing_env:" in reason for reason in payload["reasons"])
