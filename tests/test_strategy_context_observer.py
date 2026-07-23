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
from risk.risk_engine import DailyState
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


def test_accepted_bar_at_daily_capacity_still_writes_one_context_row(
    tmp_path, monkeypatch
):
    """July 22 regression: capacity returns used to occur before observation."""
    from journal.journal_logger import JournalLogger
    from webhook.runner import process_alert
    import sys

    sys.path.insert(0, "tests")
    from test_e2e_scenarios import _base_payload

    cfg = replace(load_config(), max_staleness_seconds=10 ** 9)
    monkeypatch.setattr(
        JournalLogger,
        "get_daily_state",
        lambda self, for_date=None: DailyState(trade_count=cfg.max_trades_per_day),
    )

    result = process_alert(
        _base_payload(timestamp="2026-05-23T14:30:00+00:00"),
        config=cfg,
        log_dir=str(tmp_path),
        for_date=date(2026, 5, 23),
    )

    assert result["decision"] == "BLOCKED_MAX_TRADES"
    rows = evidence_path(tmp_path).read_text().splitlines()
    assert len(rows) == 1
    row = json.loads(rows[0])
    assert row["decision"] == "BLOCKED_MAX_TRADES"
    assert row["observation_only"] is True
    assert row["gate_authoritative"] is False
    assert row["risk_evaluated"] is False
    assert row["broker_evaluated"] is False


def test_accepted_bar_with_open_position_still_writes_one_context_row(
    tmp_path, monkeypatch
):
    """Open-position blocking remains identical but no longer loses context."""
    from journal.journal_logger import JournalLogger
    from webhook.runner import process_alert
    import sys

    sys.path.insert(0, "tests")
    from test_e2e_scenarios import _base_payload

    cfg = replace(load_config(), max_staleness_seconds=10 ** 9)
    monkeypatch.setattr(
        JournalLogger,
        "get_daily_state",
        lambda self, for_date=None: DailyState(has_open_position=True),
    )
    monkeypatch.setattr(
        JournalLogger,
        "get_open_position",
        lambda self, for_date=None: {
            "instrument": "MES",
            "direction": "LONG",
            "entry": 7500.0,
            "stop": 7490.0,
            "target": 7520.0,
            "contracts": 1,
            "strategy": "orb_reclaim",
            "ts": "2026-05-23T14:00:00+00:00",
            "bar_ts": "2026-05-23T13:45:00+00:00",
        },
    )

    result = process_alert(
        _base_payload(timestamp="2026-05-23T14:30:00+00:00"),
        config=cfg,
        log_dir=str(tmp_path),
        for_date=date(2026, 5, 23),
    )

    assert result["decision"] == "BLOCKED_OPEN_POSITION"
    rows = evidence_path(tmp_path).read_text().splitlines()
    assert len(rows) == 1
    row = json.loads(rows[0])
    assert row["decision"] == "BLOCKED_OPEN_POSITION"
    assert row["observation_only"] is True
    assert row["gate_authoritative"] is False
    assert row["risk_evaluated"] is False
    assert row["broker_evaluated"] is False


def test_open_position_block_writes_both_visibility_and_context(config, tmp_path, monkeypatch):
    """Composition guard (context-feed rebased over #304): a BLOCKED_OPEN_POSITION
    bar must write BOTH the #304 BLOCK_VISIBILITY record (why evaluation was
    blocked) AND the observe-only strategy context observation (market context).
    They are distinct data on the same early-return and must not shadow each
    other."""
    from dataclasses import replace
    from datetime import date, datetime, timezone
    from journal.journal_logger import JournalLogger
    from webhook.payload import AlertPayload
    from webhook.runner import process_alert

    monkeypatch.setenv("BROKER", "paper")
    log_dir = str(tmp_path / "logs")
    today = date(2026, 5, 23)
    j = JournalLogger(log_dir=log_dir)
    j._append({
        "ts": datetime.now(timezone.utc).isoformat(), "instrument": "MNQ",
        "session": "new_york", "decision": "TRADE", "market_condition": "TRENDING",
        "context": {"timestamp": f"{today.isoformat()}T14:25:00+00:00"},
        "setup": {"direction": "LONG", "entry": 19500.0, "stop": 19460.0,
                  "target": 19580.0, "rr_ratio": 2.0, "strategy": "orb_reclaim",
                  "notes": None, "contracts": 1},
        "risk_check": {"result": "APPROVED", "failed_rule": None, "reason": None},
        "outcome": None,
    }, today)
    j.log_order_ids(instrument="MNQ", session="new_york",
                    order_ids={"entry": 111, "stop": 222, "target": 333}, for_date=today)

    payload = AlertPayload(
        ticker="MNQ1!", timestamp="2026-05-23T14:30:00+00:00", timeframe="15",
        open=19510.0, high=19560.0, low=19505.0, close=19550.0, volume=4200,
        avg_volume=3800, vwap=19495.0, orb_high=19498.0, orb_low=19462.0,
        orb_status="above", market_condition="TRENDING", trend_direction="UP",
        trend_strength="MODERATE", previous_day_high=19520.0,
        previous_day_low=19440.0, previous_day_close=19475.0)
    r = process_alert(payload, config=replace(config, paper_mode=True),
                      log_dir=log_dir, for_date=today)

    assert r["decision"] == "BLOCKED_OPEN_POSITION"
    assert r.get("block_visibility") is not None                    # #304 record
    assert r.get("strategy_context_observation") is not None        # context-feed row
    rows = j._read_entries(j._journal_path(today))
    assert len([x for x in rows if x.get("type") == "BLOCK_VISIBILITY"]) == 1
    assert (tmp_path / "logs" / "strategy_context_observations.jsonl").exists()
