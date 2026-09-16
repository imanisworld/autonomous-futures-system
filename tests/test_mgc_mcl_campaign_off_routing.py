"""Regression proof for MGC/MCL behavior while cross-instrument observation is OFF.

This intentionally does NOT restore the pre-#585 runner path. MGC/MCL are now
collection-only roots, so weakening the runner backstop would re-open access to
journal / DecisionEngine / RiskEngine / broker code. Instead we pin the safer
fail-closed contract:

* the webhook router recognizes MGC/MCL as observation-only;
* the disabled observation transport writes no campaign bars/evidence/state;
* even a direct process_alert() call cannot cross the collection-only backstop.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from execution import cross_instrument_observation as cio
from webhook.observation_transport import observe_collection_only_alert
from webhook.payload import AlertPayload
from webhook.runner import process_alert

DAY = date(2026, 9, 16)


def _payload(ticker: str, close: float) -> AlertPayload:
    return AlertPayload(
        ticker=ticker,
        timestamp=datetime(2026, 9, 16, 14, 30, tzinfo=timezone.utc).isoformat(),
        timeframe="15",
        open=close,
        high=close * 1.001,
        low=close * 0.999,
        close=close,
        volume=1000,
        avg_volume=900,
        vwap=close,
        market_condition="TRENDING",
        trend_direction="UP",
        trend_strength="MODERATE",
        previous_day_high=close * 1.01,
        previous_day_low=close * 0.99,
        previous_day_close=close,
    )


@pytest.fixture
def campaign_off(monkeypatch):
    monkeypatch.delenv(cio.ENV_NAME, raising=False)
    monkeypatch.delenv(cio.EPOCH_ENV_NAME, raising=False)
    assert cio.campaign_enabled() is False


def test_mgc_mcl_off_route_is_explicit_observation_only_not_trading(campaign_off):
    import webhook.app as app_module

    # Historical ingest recognition remains documented, but collection-only
    # precedence means these roots never fall through to the trading route.
    assert app_module._route_for_ticker("MGC1!") == "observation"
    assert app_module._route_for_ticker("MCLZ6") == "observation"
    assert app_module._route_for_ticker("MNQ1!") == "trading"
    assert app_module._route_for_ticker("MES1!") == "trading"
    assert app_module._route_for_ticker("M2K1!") is None
    assert app_module._route_for_ticker("MBT1!") is None


def test_mgc_mcl_off_transport_writes_no_campaign_data(tmp_path, config, campaign_off):
    for ticker, close in (("MGC1!", 2400.0), ("MCL1!", 65.0)):
        out = observe_collection_only_alert(
            _payload(ticker, close),
            config=config,
            log_dir=str(tmp_path),
            for_date=DAY,
        )
        assert out["decision"] == cio.DECISION_OBSERVATION_ONLY
        assert out["execution_reachable"] is False
        assert out["observation"]["enabled"] is False
        assert out["observation"]["bar_recorded"] is False
        assert out["observation"]["skipped"] == "campaign disabled"

    assert not (tmp_path / cio.EVIDENCE_FILENAME).exists()
    assert not (tmp_path / cio.STATE_FILENAME).exists()
    assert not list(tmp_path.glob("bars_MGC_*.jsonl"))
    assert not list(tmp_path.glob("bars_MCL_*.jsonl"))


def test_direct_runner_call_cannot_reopen_mgc_mcl_execution_path(
    tmp_path, config, campaign_off, monkeypatch
):
    from execution.paper_broker import PaperBroker
    from risk.risk_engine import RiskEngine
    from strategy.signal_engine import DecisionEngine

    def boom(*args, **kwargs):
        raise AssertionError("MGC/MCL crossed collection-only execution boundary")

    monkeypatch.setattr(DecisionEngine, "evaluate", boom)
    monkeypatch.setattr(RiskEngine, "validate", boom)
    monkeypatch.setattr(PaperBroker, "execute_bracket", boom)
    monkeypatch.setattr(PaperBroker, "resolve_position", boom)

    for ticker, close in (("MGC1!", 2400.0), ("MCL1!", 65.0)):
        out = process_alert(
            _payload(ticker, close),
            config=config,
            log_dir=str(tmp_path),
            for_date=DAY,
        )
        assert out["decision"] == cio.DECISION_OBSERVATION_ONLY
        assert out["execution_reachable"] is False
        assert out["risk"] is None
        assert out["fill"] is None

    # Backstop returns before journal / campaign / BarHistory writes.
    assert not list(tmp_path.glob("journal_*.jsonl"))
    assert not (tmp_path / cio.EVIDENCE_FILENAME).exists()
    assert not list(tmp_path.glob("bars_MGC_*.jsonl"))
    assert not list(tmp_path.glob("bars_MCL_*.jsonl"))
