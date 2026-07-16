import json
from dataclasses import replace
from datetime import date
from pathlib import Path

from context.bar_history import BarHistory
from context.market_context import KeyLevels, SupplyDemandData
from context.strategy_context_observer import (
    append_strategy_context_observation,
    evidence_path,
)
from config.settings import load_config
from strategy.signal_engine import DecisionOutput, SetupDetail


def _bars(up=True):
    closes = (
        [100.0, 101.0, 102.0, 103.0, 104.0]
        if up
        else [104.0, 103.0, 102.0, 101.0, 100.0]
    )
    bars = []
    for idx, close in enumerate(closes):
        minutes = idx * 15
        bars.append(
            {
                "ts": (
                    f"2026-05-23T{14 + minutes // 60:02d}:"
                    f"{minutes % 60:02d}:00+00:00"
                ),
                "open": close - 0.5,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "timeframe": "15m",
            }
        )
    return bars


def _decision(state):
    return DecisionOutput(
        timestamp=state.timestamp,
        instrument=state.instrument,
        session=state.session,
        decision="TRADE",
        reason="test decision",
        market_condition=state.market_condition,
        setup=SetupDetail(
            strategy="orb_reclaim",
            direction="LONG",
            entry=19501.0,
            stop=19480.0,
            target=19543.0,
            rr_ratio=2.0,
        ),
        failed_gates=[],
    )


def test_context_observer_records_requested_variables(tmp_path, fresh_market_state):
    state = fresh_market_state
    state.raw = {"overnight_high": 19500.0, "overnight_low": 19400.0}
    state.sd = SupplyDemandData(demand_top=19508.0, demand_bottom=19490.0)
    state.key_levels = KeyLevels(hod=19506.0, lod=19400.0)
    hist = BarHistory(log_dir=str(tmp_path))
    for bar in _bars(up=True):
        hist.record("MES", **bar, for_date=date(2026, 5, 23))

    row = append_strategy_context_observation(
        log_dir=tmp_path,
        state=state,
        decision=_decision(state),
        recent_bars=_bars(up=True),
        for_date=date(2026, 5, 23),
    )

    assert row["observation_only"] is True
    assert row["gate_authoritative"] is False
    assert row["broker_evaluated"] is False
    assert row["trend_persistence"]["direction"] == "UP"
    assert row["mnq_mes_agreement"]["agrees"] is True
    assert row["overnight_range_location"]["location"] == "above_overnight_range"
    assert row["supply_demand_confluence"]["zone"] == "in_demand"
    assert row["key_level_confluence"]["nearest"]["name"] == "hod"
    assert row["impulse_state"]["state"] == "late_entry"
    saved = json.loads(evidence_path(tmp_path).read_text().splitlines()[0])
    assert saved["selected_setup"]["strategy"] == "orb_reclaim"


def test_context_observer_has_no_tradovate_dependency():
    source = Path("context/strategy_context_observer.py").read_text()
    assert "tradovate" not in source.lower()
    assert "execute_bracket" not in source


def test_runner_writes_context_observation_without_changing_trade(tmp_path):
    from webhook.runner import process_alert
    import sys

    sys.path.insert(0, "tests")
    from test_e2e_scenarios import _base_payload

    payload = _base_payload(timestamp="2026-05-23T14:30:00+00:00")
    payload.ticker = "MNQ"
    cfg = replace(load_config(), max_staleness_seconds=10 ** 9)
    result = process_alert(
        payload,
        config=cfg,
        log_dir=str(tmp_path),
        for_date=date(2026, 5, 23),
    )

    assert result["decision"] == "TRADE"
    assert result["strategy_context_observation"]["observation_only"] is True
    row = json.loads(evidence_path(tmp_path).read_text().splitlines()[0])
    assert row["instrument"] == "MNQ"
    assert row["risk_evaluated"] is False
    assert row["broker_evaluated"] is False
