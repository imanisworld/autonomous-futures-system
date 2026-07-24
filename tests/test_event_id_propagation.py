"""
tests/test_event_id_propagation.py

Regression for the event_id traceability gap: webhook/app.py::receive_alert
minted or preserved a correlation id via ensure_event_id(), but only ever
used the local variable for its own ack/log lines — it was never written
back onto `payload`, so webhook/runner.py::process_alert (and everything it
journals) never saw it. `result.get("event_id")` at the two existing
_candidate_snapshot(event_id=...) call sites was always None.

event_id identifies one webhook delivery/bar-close alert — NOT a position.
It must never be used to join TRADE and OUTCOME journal rows (that's
paper_order_id's job, see adaptive/journal_reader.py and PR #327); this
file only proves it is a passthrough trace id with no effect on decisions.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest

from webhook.payload import AlertPayload

from tests.test_webhook import _base_payload, _isolate_app_logs


# ─── Ingress: receive_alert mints/preserves and assigns onto payload ─────────

def _run_receive_alert(monkeypatch, tmp_path, payload: AlertPayload):
    """Call receive_alert directly (no HTTP layer) with process_alert
    stubbed out and its background task drained, so only the ingress
    mint-or-preserve-and-assign logic is under test."""
    import webhook.app as app_module
    from webhook.app import receive_alert

    _isolate_app_logs(monkeypatch, tmp_path)
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    monkeypatch.setattr(
        app_module, "process_alert",
        lambda *a, **k: {"ok": True, "decision": "NO_TRADE", "context": None},
    )

    async def _call():
        resp = await receive_alert(
            payload, request=None, x_webhook_secret="test-secret", secret=None,
        )
        for task in list(app_module._alert_tasks):
            await task
        return resp

    resp = asyncio.run(_call())
    return json.loads(resp.body)


def test_receive_alert_mints_and_assigns_event_id_onto_payload(monkeypatch, tmp_path):
    payload = _base_payload()
    assert payload.event_id is None

    data = _run_receive_alert(monkeypatch, tmp_path, payload)

    assert isinstance(data.get("event_id"), str) and data["event_id"]
    # The core regression: the ingress id is written back onto the payload
    # object itself, not just used for the local ack response.
    assert payload.event_id == data["event_id"]


def test_receive_alert_preserves_a_valid_supplied_event_id(monkeypatch, tmp_path):
    payload = _base_payload(event_id="tv-alert-8f3c1a")

    data = _run_receive_alert(monkeypatch, tmp_path, payload)

    assert data["event_id"] == "tv-alert-8f3c1a"
    assert payload.event_id == "tv-alert-8f3c1a"


def test_two_alerts_get_two_different_generated_event_ids(monkeypatch, tmp_path):
    data1 = _run_receive_alert(monkeypatch, tmp_path, _base_payload())
    data2 = _run_receive_alert(
        monkeypatch, tmp_path,
        _base_payload(timestamp="2026-05-23T14:45:00+00:00"),
    )

    assert data1["event_id"] != data2["event_id"]


# ─── Downstream: process_alert propagates payload.event_id into result/journal ─

def test_early_rejection_result_and_journal_carry_event_id(config, tmp_path):
    """BLOCKED_DATA_QUALITY is one of process_alert's early-return dict
    literals (not the main `result` dict) — it must carry event_id too."""
    from webhook.runner import process_alert

    log_dir = str(tmp_path / "logs")
    payload = _base_payload(high=100.0, low=200.0, event_id="evt-bad-ohlc")

    result = process_alert(payload, config=config, log_dir=log_dir)

    assert result["decision"] == "BLOCKED_DATA_QUALITY"
    assert result["event_id"] == "evt-bad-ohlc"

    journal_path = next((tmp_path / "logs").glob("journal_*.jsonl"))
    entry = json.loads(journal_path.read_text().splitlines()[-1])
    assert entry["decision"] == "BLOCKED_DATA_QUALITY"
    assert entry["event_id"] == "evt-bad-ohlc"


def test_confirmed_trade_result_and_journal_carry_event_id(config, tmp_path):
    from webhook.runner import process_alert

    cfg = replace(config, enabled_concepts=config.enabled_concepts + ["orb_breakout"])
    log_dir = str(tmp_path / "logs")
    payload = _base_payload(
        ticker="MES1!",
        open=5885.0, high=5901.0, low=5880.0, close=5900.0,
        volume=5000, avg_volume=3800, vwap=5895.0,
        orb_high=5898.0, orb_low=5862.0, orb_status="above",
        previous_day_high=5920.0, previous_day_low=5840.0, previous_day_close=5875.0,
        event_id="evt-mes-orb-1",
    )

    result = process_alert(payload, config=cfg, log_dir=log_dir)

    assert result["decision"] == "TRADE"
    assert result["event_id"] == "evt-mes-orb-1"

    journal_path = next((tmp_path / "logs").glob("journal_*.jsonl"))
    lines = [json.loads(line) for line in journal_path.read_text().splitlines()]
    # Every journaled decision for this alert (TRADE_INTENT then the
    # confirmed TRADE row) must carry the same event_id — not just the
    # final row.
    decision_rows = [e for e in lines if e.get("decision") in ("TRADE_INTENT", "TRADE")]
    assert decision_rows
    assert all(e["event_id"] == "evt-mes-orb-1" for e in decision_rows)
    assert decision_rows[-1]["decision"] == "TRADE"


def test_event_id_does_not_change_decision_or_routing(config, tmp_path):
    """event_id must be a pure passthrough trace id: two otherwise-identical
    alerts differing only in event_id must reach the identical decision —
    no gate anywhere may branch on it."""
    from webhook.runner import process_alert

    cfg = replace(config, enabled_concepts=config.enabled_concepts + ["orb_breakout"])
    kwargs = dict(
        ticker="MES1!",
        open=5885.0, high=5901.0, low=5880.0, close=5900.0,
        volume=5000, avg_volume=3800, vwap=5895.0,
        orb_high=5898.0, orb_low=5862.0, orb_status="above",
        previous_day_high=5920.0, previous_day_low=5840.0, previous_day_close=5875.0,
    )

    result_a = process_alert(
        _base_payload(event_id="evt-a", **kwargs),
        config=cfg, log_dir=str(tmp_path / "logs_a"),
    )
    result_b = process_alert(
        _base_payload(event_id="evt-b", **kwargs),
        config=cfg, log_dir=str(tmp_path / "logs_b"),
    )
    result_none = process_alert(
        _base_payload(**kwargs),
        config=cfg, log_dir=str(tmp_path / "logs_none"),
    )

    for r in (result_a, result_b, result_none):
        assert r["decision"] == "TRADE"
        assert r["risk"]["result"] == "APPROVED"
        assert r["fill"]["entry"] == result_a["fill"]["entry"]
        assert r["fill"]["stop"] == result_a["fill"]["stop"]
        assert r["fill"]["target"] == result_a["fill"]["target"]

    assert result_a["event_id"] == "evt-a"
    assert result_b["event_id"] == "evt-b"
    assert result_none["event_id"] is None
