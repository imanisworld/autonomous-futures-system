"""Integration: the observe-only range collector.

Proves the runner injects the right journal keys when `range_observe_enabled`
is ON, injects nothing when it's OFF (default), and NEVER changes the decision.
Journal-only by construction — these assertions are the live-box contract.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from config.settings import load_config
from journal.journal_logger import JournalLogger
from tests.test_e2e_scenarios import _base_payload as _payload
from webhook.runner import process_alert

_DAY = date(2026, 5, 23)


def _cfg(range_on: bool):
    # Relax staleness so the fixed-timestamp fixture bar isn't rejected.
    return replace(
        load_config(),
        max_staleness_seconds=10_000_000,
        range_observe_enabled=range_on,
    )


def _last_entry(log_dir):
    j = JournalLogger(log_dir=log_dir)
    entries = j._read_entries(j._journal_path(_DAY))
    assert entries, "expected at least one journal entry"
    return entries[-1]


def test_range_keys_present_on_range_bound_no_trade_when_enabled(tmp_path):
    log_dir = str(tmp_path / "logs")
    # RANGE_BOUND label → blocked by the TRENDING-only gate → NO_TRADE path.
    payload = _payload(market_condition="RANGE_BOUND", trend_strength="MODERATE")
    process_alert(payload, config=_cfg(True), log_dir=log_dir, for_date=_DAY)
    e = _last_entry(log_dir)
    assert e["decision"] != "TRADE"
    assert "wall_context" in e
    assert "range_state" in e
    assert "range_signal" in e


def test_shadow_range_signal_present_on_setup_path_when_enabled(tmp_path):
    log_dir = str(tmp_path / "logs")
    # TRENDING / reclaimed_high default → reaches the setup (TRADE/RISK_REJECTED) path.
    process_alert(_payload(), config=_cfg(True), log_dir=log_dir, for_date=_DAY)
    e = _last_entry(log_dir)
    assert "shadow_range_signal" in e
    assert "wall_context" in e


def test_no_range_keys_when_disabled(tmp_path):
    log_dir = str(tmp_path / "logs")
    payload = _payload(market_condition="RANGE_BOUND", trend_strength="MODERATE")
    process_alert(payload, config=_cfg(False), log_dir=log_dir, for_date=_DAY)
    e = _last_entry(log_dir)
    for k in ("wall_context", "range_state", "range_signal", "shadow_range_signal"):
        assert k not in e, f"{k} must be absent when range_observe_enabled is off"


def test_collector_never_changes_the_decision(tmp_path):
    payload = _payload()
    off = process_alert(payload, config=_cfg(False), log_dir=str(tmp_path / "a"), for_date=_DAY)
    on = process_alert(payload, config=_cfg(True), log_dir=str(tmp_path / "b"), for_date=_DAY)
    assert off["decision"] == on["decision"]
    assert (off.get("failed_gates") or []) == (on.get("failed_gates") or [])
    assert off.get("confidence_score") == on.get("confidence_score")


def test_blocked_candidate_audit_never_reaches_risk_or_broker(monkeypatch, tmp_path):
    import webhook.runner as runner

    class _RiskMustNotBeConstructed:
        def __init__(self, *args, **kwargs):
            raise AssertionError("risk path must remain unreachable")

    def _broker_must_not_execute(*args, **kwargs):
        raise AssertionError("broker path must remain unreachable")

    monkeypatch.setattr(runner, "RiskEngine", _RiskMustNotBeConstructed)
    monkeypatch.setattr(runner.PaperBroker, "execute_bracket", _broker_must_not_execute)

    log_dir = str(tmp_path / "logs")
    payload = _payload(market_condition="RANGE_BOUND", trend_strength="MODERATE")
    result = process_alert(
        payload,
        config=_cfg(True),
        log_dir=log_dir,
        for_date=_DAY,
    )

    assert result["decision"] == "NO_TRADE"
    assert result["failed_gates"] == ["MARKET_CONDITION_NOT_TRENDING"]
    entry = _last_entry(log_dir)
    assert entry["decision"] == "NO_TRADE"
    assert entry["setup"] is None
    assert entry["candidate_audit"] == []
    assert entry["blocked_candidate_audit"]["observation_only"] is True
    assert entry["blocked_candidate_audit"]["risk_evaluated"] is False
    assert entry["blocked_candidate_audit"]["broker_evaluated"] is False
