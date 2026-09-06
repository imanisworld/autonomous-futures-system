from __future__ import annotations

from pathlib import Path

from alert_ranker.app import _render_scanner_dashboard, create_app
from alert_ranker.config import ScannerConfig
from alert_ranker.discord import build_discord_payload
from alert_ranker.scorer import ScoreResult


def _config(tmp_path: Path) -> ScannerConfig:
    return ScannerConfig(
        market_data_provider="public",
        tastytrade_username="",
        tastytrade_password="",
        tastytrade_base_url="https://api.tastyworks.com",
        public_api_key_configured=False,
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
    )


def test_broken_signa_auto_routes_are_absent(tmp_path):
    app = create_app(_config(tmp_path))
    paths = {route.path for route in app.routes}

    assert "/rh-options/evaluate-auto" not in paths
    assert "/rh-options/rank-and-evaluate" not in paths


def test_dashboard_never_uses_signa_pivots_or_regime_as_gex():
    html = _render_scanner_dashboard()

    assert "raw.signa_pivot_s1" not in html
    assert "raw.signa_pivot_r1" not in html
    assert "raw._pivot_s1" not in html
    assert "raw._pivot_r1" not in html
    assert "raw.regime_class" not in html
    assert "const support = raw.gex_support_wall;" in html
    assert "const resistance = raw.gex_resistance_wall;" in html
    assert "const regime = raw.gex_regime || raw.gex_note || '-';" in html


def test_app_source_has_no_removed_auto_authority_or_default_gex_regime():
    from alert_ranker import app as app_module
    import inspect

    source = inspect.getsource(app_module)
    assert "rh_options_evaluate_auto" not in source
    assert "rh_options_rank_and_evaluate" not in source
    assert "rank_option_contracts" not in source
    assert "LOW_PINNING" not in source


def test_untriggered_discord_ignores_legacy_confirmed_copy():
    result = ScoreResult(
        ticker="AAPL",
        direction="LONG",
        score=9,
        pattern="2-1-2",
        components={"strat_pattern": 3, "vwap": 2, "trend": 2, "volume": 2, "signa": 0},
        raw={
            "setup_status": "WATCH",
            "thesis": "A+ CONFIRMED GOLDEN SETUP",
            "why": "All gates passed",
            "edge": "Multi-timeframe alignment confirmed",
            "risk": "Enter now and size up",
        },
    )

    embed = build_discord_payload(result)["embeds"][0]
    rendered = " ".join(
        [embed["title"], embed["description"]]
        + [str(field["value"]) for field in embed["fields"]]
    )

    assert "SETUP WATCHING" in embed["title"]
    assert "A+ CONFIRMED" not in rendered
    assert "All gates passed" not in rendered
    assert "alignment confirmed" not in rendered
    assert "Enter now" not in rendered
    assert "No entry" in rendered
