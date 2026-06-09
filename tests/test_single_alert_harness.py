from __future__ import annotations

import json
import logging
from pathlib import Path

from scripts import replay_single_alert
from webhook.payload import AlertPayload
from webhook.state_builder import build_market_state

FIXTURE = Path("tradingview/single_alert_regression_fixture.json")


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


def test_regression_fixture_carries_key_level_and_sd_fields():
    """Guard the stale-fixture blind spot: the fixture must carry EMA/HOD/LOD and
    S&D zone fields, and they must flow through state_builder into populated
    KeyLevels + SupplyDemandData. If a future edit strips these, the harness would
    silently exercise a degraded (None key_levels) path again — this fails first."""
    raw = json.loads(FIXTURE.read_text())

    # Fields the live key-level / S&D build depends on must be present and non-null.
    for field in ("ema_9", "ema_21", "ema_55", "ema_200", "hod", "lod",
                  "supply_top", "supply_bottom", "demand_top", "demand_bottom"):
        assert raw.get(field) is not None, f"fixture missing key field: {field}"

    state = build_market_state(AlertPayload.model_validate(raw))

    assert state.key_levels is not None, "key_levels did not build from fixture"
    assert state.key_levels.ema_9 == raw["ema_9"]
    assert state.key_levels.ema_9_above_21 is True
    assert state.key_levels.price_above_ema_55 is True
    assert state.key_levels.hod == raw["hod"]

    assert state.sd is not None, "supply/demand did not build from fixture"
    assert state.sd.supply_top == raw["supply_top"]
    assert state.sd.demand_bottom == raw["demand_bottom"]


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
