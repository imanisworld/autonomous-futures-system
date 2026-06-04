from __future__ import annotations

import logging
from pathlib import Path

from scripts import replay_single_alert


def test_summarize_result_keeps_decision_risk_fill_fields():
    data = {
        "ok": True,
        "decision": "RISK_REJECTED",
        "resolution": None,
        "failed_gates": ["Trend strength below required"],
        "risk": {"result": "REJECTED", "failed_rule": "max_contracts"},
        "fill": {"status": "CANCELLED"},
        "confidence_score": 72,
        "confluence": {"grade": "B"},
        "extra": "ignored",
    }

    assert replay_single_alert.summarize_result(data) == {
        "ok": True,
        "decision": "RISK_REJECTED",
        "resolution": None,
        "failed_gates": ["Trend strength below required"],
        "risk": {"result": "REJECTED", "failed_rule": "max_contracts"},
        "fill": {"status": "CANCELLED"},
        "confidence_score": 72,
        "confluence": {"grade": "B"},
    }


def test_diagnostic_lines_filters_bracket_and_cancel_messages():
    logger = logging.getLogger("test.harness")
    records = [
        logger.makeRecord("test.harness", logging.INFO, __file__, 1, "ordinary info", (), None),
        logger.makeRecord("test.harness", logging.ERROR, __file__, 2, "Tradovate bracket placed", (), None),
        logger.makeRecord("test.harness", logging.WARNING, __file__, 3, "cancel: /order/list failed", (), None),
    ]

    assert replay_single_alert.diagnostic_lines(records) == [
        "ERROR:test.harness:Tradovate bracket placed",
        "WARNING:test.harness:cancel: /order/list failed",
    ]


def test_harness_replays_sample_fixture_in_paper_mode(tmp_path, capsys):
    fixture = Path("tradingview/single_alert_regression_fixture.json")
    rc = replay_single_alert.main([
        str(fixture),
        "--log-dir", str(tmp_path / "logs"),
        "--broker", "paper",
    ])

    out = capsys.readouterr().out
    assert rc == 0
    assert "== Decision Summary ==" in out
    assert '"decision"' in out
    assert "== Latest Webhook ==" in out
