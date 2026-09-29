"""SPX→SPXW lane isolation: not in equity universe, no live order path."""

from __future__ import annotations

import ast
import asyncio
import inspect
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from alert_ranker import paper_spxw_v1 as spxw
from alert_ranker import paper_v1 as equity_v1
from alert_ranker.app import create_app
from alert_ranker.config import ScannerConfig, load_config
from alert_ranker.spxw_lane import (
    SpxwPaperLane,
    assert_isolated_from_equity_universe,
    build_spxw_paper_candidate,
    equity_universe_contains_spx_or_spxw,
)
from alert_ranker.spxw_storage import SpxwStorage, build_spxw_rollup
from ops.options_spxw_daily_pnl_report import build_report, format_digest

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 29, 10, 30, tzinfo=NY)


def test_default_equity_watchlist_excludes_spx_and_spxw(monkeypatch):
    monkeypatch.delenv("OPTIONS_SCANNER_WATCHLIST", raising=False)
    monkeypatch.delenv("OPTIONS_SPXW_PAPER_LANE_ENABLED", raising=False)
    cfg = load_config([])
    assert "SPX" not in cfg.watchlist
    assert "SPXW" not in cfg.watchlist
    assert cfg.spxw_paper_lane_enabled is False
    assert equity_universe_contains_spx_or_spxw(cfg.watchlist) is False


def test_isolation_assert_rejects_equity_watchlist_with_spx():
    with pytest.raises(RuntimeError, match="spxw_lane_isolation_violation"):
        assert_isolated_from_equity_universe(["SPY", "SPX", "AAPL"])


def test_lane_module_has_no_broker_order_imports():
    """AST-only boundary: no executable broker/order imports or submit calls.

    Do not embed forbid-list module path strings in ``spxw_lane.py`` itself —
    a repo-wide source scan treats those literals as an import boundary breach.
    """
    source_path = Path(inspect.getfile(SpxwPaperLane))
    tree = ast.parse(source_path.read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported.add(module)
            for alias in node.names:
                imported.add(f"{module}.{alias.name}" if module else alias.name)
    forbidden_prefixes = ("options_manager.adapters", "options_manager.order")
    for name in imported:
        assert not any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden_prefixes)
    assert "PreparedOrderTicket" not in imported
    call_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "submit_order" not in call_names
    # Source must not mention the sandbox paper-order module path at all.
    assert "sandbox_paper" not in source_path.read_text()


class _Chain:
    def __init__(self, expiration: str, calls):
        self.expiration = expiration
        self.calls = calls
        self.puts = ()
        self.error = None


class _Market:
    async def fetch_option_expirations(self, ticker: str):
        assert ticker == "SPXW"
        return ["2026-09-29", "2026-10-03"]

    async def fetch_option_chain(self, ticker: str, expiration: str | None = None):
        assert ticker == "SPXW"
        quote = SimpleNamespace(
            symbol=f"SPXW{expiration.replace('-', '')[2:]}C05800000",
            option_type="CALL",
            strike=5800.0,
            bid=1.40,
            ask=1.50,
            mid=1.45,
            volume=2000,
            open_interest=8000,
            delta=0.40,
            gamma=0.01,
            theta=-0.05,
            implied_volatility=0.18,
            quote_timestamp="2026-09-29T14:29:30+00:00",
            source="test",
            stale=False,
        )
        return _Chain(expiration, (quote,))


def _triggered_spx_setup(**overrides):
    data = {
        "ticker": "SPX",
        "signal_underlying": "SPX",
        "setup_status": "TRIGGERED",
        "direction": "LONG",
        "price": 5800.0,
        "pattern": "2-1-2",
        "setup_type": "2-1-2",
        "underlying_invalidation": 5780.0,
        "target_1": 5850.0,
    }
    data.update(overrides)
    return data


def test_triggered_spx_setup_builds_spxw_candidate():
    result = asyncio.run(
        build_spxw_paper_candidate(
            market_data=_Market(),
            setup=_triggered_spx_setup(),
            now=NOW,
        )
    )
    assert result.status == "OPEN"
    assert result.paper_policy_id == "OPTIONS_PAPER_SPXW_V1"
    assert result.signal_underlying == "SPX"
    assert result.contract_root == "SPXW"
    assert result.dte_cohort == spxw.COHORT_0DTE
    assert result.selected_contract["contract_multiplier"] == 100
    assert result.selected_contract["planned_risk_dollars"] == 37.5


def test_lane_journals_open_row_when_enabled(tmp_path):
    cfg = SimpleNamespace(
        spxw_paper_lane_enabled=True,
        timezone="America/New_York",
        public_stale_quote_seconds=900.0,
        paper_v1_min_remaining_rr=1.0,
        spxw_interval_minutes=5,
        spxw_sqlite_path=tmp_path / "spxw.sqlite",
    )
    storage = SpxwStorage(cfg.spxw_sqlite_path)
    lane = SpxwPaperLane(
        config=cfg,
        market_data=_Market(),
        storage=storage,
        equity_watchlist=["SPY", "QQQ", "AAPL"],
    )
    result = asyncio.run(lane.scan_spx_setup(_triggered_spx_setup(), now=NOW))
    assert result.status == "OPEN"
    assert result.journal_id is not None
    rows = storage.latest(limit=5)
    assert len(rows) == 1
    assert rows[0].signal_underlying == "SPX"
    assert rows[0].contract_root == "SPXW"
    assert rows[0].dte_cohort == "0DTE"
    assert rows[0].selected_contract["paper_policy_id"] == spxw.POLICY_ID


def test_lane_disabled_by_default_skips(tmp_path):
    cfg = SimpleNamespace(
        spxw_paper_lane_enabled=False,
        timezone="America/New_York",
        public_stale_quote_seconds=900.0,
        paper_v1_min_remaining_rr=1.0,
        spxw_interval_minutes=5,
        spxw_sqlite_path=tmp_path / "spxw.sqlite",
    )
    lane = SpxwPaperLane(
        config=cfg,
        market_data=_Market(),
        storage=SpxwStorage(cfg.spxw_sqlite_path),
        equity_watchlist=["SPY"],
    )
    result = asyncio.run(lane.scan_spx_setup(_triggered_spx_setup(), now=NOW))
    assert result.status == "SKIPPED"
    assert result.reason == "spxw_lane_disabled"


def test_rollup_never_marks_equity_mix(tmp_path):
    storage = SpxwStorage(tmp_path / "spxw.sqlite")
    storage.record(
        direction="LONG",
        pattern="2-1-2",
        setup_type="2-1-2",
        status="WIN",
        dte_cohort="0DTE",
        dte=0,
        selected_contract={
            "planned_risk_dollars": 37.5,
            "spread_cost_dollars": 10.0,
        },
        outcome={"pnl_dollars": 50.0},
    )
    storage.record(
        direction="SHORT",
        pattern="2-1-2",
        setup_type="2-1-2",
        status="LOSS",
        dte_cohort="1_PLUS_DTE",
        dte=4,
        selected_contract={
            "planned_risk_dollars": 40.0,
            "spread_cost_dollars": 12.0,
        },
        outcome={"pnl_dollars": -25.0},
    )
    rollup = build_spxw_rollup(storage.all_rows())
    assert rollup["mixed_with_equity_universe"] is False
    assert rollup["by_cohort"]["0DTE"]["pnl_dollars"] == 50.0
    assert rollup["by_cohort"]["1_PLUS_DTE"]["pnl_dollars"] == -25.0
    report = build_report(tmp_path / "spxw.sqlite")
    digest = format_digest(report)
    assert "OPTIONS_PAPER_SPXW_V1" in digest
    assert "isolated from equity" in digest


def test_non_spx_signal_rejected():
    result = asyncio.run(
        build_spxw_paper_candidate(
            market_data=_Market(),
            setup=_triggered_spx_setup(ticker="SPY", signal_underlying="SPY"),
            now=NOW,
        )
    )
    assert result.status == "REJECTED"
    assert result.reason == "signal_underlying_not_spx"


def _scanner_config(tmp_path: Path, **overrides) -> ScannerConfig:
    base = ScannerConfig(
        market_data_provider="tastytrade",
        tastytrade_username="user",
        tastytrade_password="pass",
        tastytrade_base_url="https://api.tastyworks.com",
        public_api_key_configured=False,
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
        spxw_paper_lane_enabled=False,
        spxw_sqlite_path=tmp_path / "options_spxw_scanner.sqlite",
    )
    return replace(base, **overrides) if overrides else base


def test_disabled_lane_creates_no_spxw_db_or_scheduler_job(tmp_path):
    spxw_db = tmp_path / "options_spxw_scanner.sqlite"
    cfg = _scanner_config(tmp_path, spxw_paper_lane_enabled=False, spxw_sqlite_path=spxw_db)
    app = create_app(cfg)
    with TestClient(app) as client:
        status = client.get("/spxw/status").json()
        assert status["lane"] == "SPX_SPXW_PAPER"
        assert status["enabled"] is False
        assert status["available"] is False
        assert status["live_execution"] is False
        assert status["broker_order_path"] is False
        health = client.get("/health").json()
        assert health["scheduler_running"] is True
        assert getattr(client.app.state, "spxw_lane", None) is None
        scheduler = getattr(client.app.state, "scheduler", None)
        assert scheduler is not None
        job_ids = {job.id for job in scheduler.get_jobs()}
        assert "options-watchlist-scan" in job_ids
        assert "options-spx-spxw-scan" not in job_ids
        assert "options-spx-spxw-resolve" not in job_ids
    assert not spxw_db.exists()
    assert not spxw_db.with_suffix(".sqlite-journal").exists()


def test_disabled_run_scheduled_scan_skips_without_journal(tmp_path):
    cfg = SimpleNamespace(
        spxw_paper_lane_enabled=False,
        timezone="America/New_York",
        public_stale_quote_seconds=900.0,
        paper_v1_min_remaining_rr=1.0,
        spxw_interval_minutes=5,
        spxw_sqlite_path=tmp_path / "spxw.sqlite",
    )
    storage = SpxwStorage(cfg.spxw_sqlite_path)
    lane = SpxwPaperLane(
        config=cfg,
        market_data=_Market(),
        storage=storage,
        equity_watchlist=["SPY"],
    )
    result = asyncio.run(lane.run_scheduled_scan(now=NOW))
    assert result.status == "SKIPPED"
    assert result.reason == "spxw_lane_disabled"
    assert storage.all_rows() == []


def test_equity_paper_v1_policy_untouched_by_spxw_module():
    assert equity_v1.POLICY_ID == "OPTIONS_PAPER_V1"
    assert equity_v1.MIN_DTE == 14
    assert equity_v1.MAX_TRADE_RISK_DOLLARS == 300.0
    assert equity_v1.MAX_AGGREGATE_OPEN_RISK_DOLLARS == 1000.0
    assert equity_v1.CONTRACT_MULTIPLIER == 100
    assert spxw.POLICY_ID == "OPTIONS_PAPER_SPXW_V1"
    assert spxw.POLICY_ID != equity_v1.POLICY_ID


def test_provider_preflight_fails_closed_without_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("PUBLIC_API_SECRET_KEY", raising=False)
    monkeypatch.delenv("PUBLIC_API_KEY", raising=False)
    monkeypatch.delenv("PUBLIC_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("OPTIONS_SPXW_PAPER_LANE_ENABLED", raising=False)
    from scripts.options_spxw_provider_preflight import main

    out = tmp_path / "spxw-provider.json"
    code = main(["--json", "--out", str(out)])
    assert code == 1
    payload = out.read_text()
    assert '"verdict": "FAIL"' in payload
    assert "missing_env:PUBLIC" in payload or "market_data_unconfigured" in payload
    assert '"stubs_accepted": false' in payload
    assert '"spxw_journal_written": false' in payload
    assert '"discord_side_effects": false' in payload
