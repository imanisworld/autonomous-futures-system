from __future__ import annotations

from types import SimpleNamespace

from webhook.runner import _candidate_snapshot


def _setup():
    return SimpleNamespace(
        direction="LONG",
        strategy="pdh_reclaim",
        entry=5300.25,
        stop=5295.25,
        target=5312.75,
        rr_ratio=2.5,
    )


def test_candidate_snapshot_is_explicitly_audit_only():
    snapshot = _candidate_snapshot(
        setup=_setup(),
        instrument="MES",
        session="new_york",
        timeframe="15",
        reject_code="NO_TRADE",
        reject_reason="entry detached",
        blocking_gate="ENTRY_DETACHED_FROM_PRICE",
        event_id="evt-1",
    )

    assert snapshot == {
        "symbol": "MES",
        "direction": "LONG",
        "strategy": "pdh_reclaim",
        "entry": 5300.25,
        "stop": 5295.25,
        "target": 5312.75,
        "contracts": None,
        "timeframe": "15",
        "session": "new_york",
        "rr": 2.5,
        "reject_code": "NO_TRADE",
        "reject_reason": "entry detached",
        "blocking_gate": "ENTRY_DETACHED_FROM_PRICE",
        "no_trade_taken": True,
        "missing_fields": [],
        "event_id": "evt-1",
    }


def test_candidate_snapshot_uses_final_risk_prices_and_reports_missing_fields():
    setup = _setup()
    setup.strategy = None
    snapshot = _candidate_snapshot(
        setup=setup,
        instrument="MNQ",
        session=None,
        timeframe="5m",
        reject_code="max_daily_loss",
        reject_reason="daily loss limit reached",
        blocking_gate="max_daily_loss",
        contracts=2,
        entry=20000.0,
        stop=19980.0,
        target=20050.0,
    )

    assert snapshot["entry"] == 20000.0
    assert snapshot["contracts"] == 2
    assert snapshot["no_trade_taken"] is True
    assert snapshot["missing_fields"] == ["strategy", "session"]
