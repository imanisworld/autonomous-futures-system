from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from execution.broker_interface import Position
from execution.day_only_exit import (
    DAY_ONLY_EXIT_REASON,
    build_day_only_fill,
    fallback_is_authorized,
    is_exact_eod_bar,
    positions_agree,
    resolve_paper_eod,
)
from execution.paper_broker import NextBarOHLC, PaperBroker
from journal.journal_logger import JournalLogger
from webhook import app as app_module


def _paper_position(*, direction: str = "LONG", strategy: str = "strat_4hr_retrigger"):
    broker = PaperBroker(starting_balance=1_500)
    broker.restore_position(
        instrument="MNQ",
        direction=direction,
        entry=100.0,
        stop=95.0 if direction == "LONG" else 105.0,
        target=110.0 if direction == "LONG" else 90.0,
        contracts=1,
    )
    position = {
        "instrument": "MNQ",
        "direction": direction,
        "entry": 100.0,
        "contracts": 1,
        "strategy": strategy,
    }
    return broker, position


def test_exact_eod_bar_est_and_edt_without_fixed_utc_assumption():
    assert is_exact_eod_bar("2026-01-12T20:55:00Z", "5m")
    assert is_exact_eod_bar("2026-07-13T19:55:00Z", "5")
    assert not is_exact_eod_bar("2026-01-12T19:55:00Z", "5m")
    assert not is_exact_eod_bar("2026-07-13T20:55:00Z", "5m")
    assert fallback_is_authorized(datetime(2026, 1, 12, 21, 0, tzinfo=timezone.utc))
    assert fallback_is_authorized(datetime(2026, 7, 13, 20, 0, tzinfo=timezone.utc))


def test_paper_eod_long_short_and_breakeven_classification():
    cases = [
        ("LONG", 101.0, "WIN"),
        ("LONG", 99.0, "LOSS"),
        ("SHORT", 99.0, "WIN"),
        ("SHORT", 101.0, "LOSS"),
        ("LONG", 100.0, "BREAKEVEN"),
    ]
    for direction, close, expected in cases:
        broker, position = _paper_position(direction=direction)
        fill = resolve_paper_eod(
            broker,
            position,
            timestamp="2026-07-13T19:55:00Z",
            timeframe="5m",
            close=close,
        )
        assert fill is not None
        assert fill.result == expected
        assert fill.exit_reason == DAY_ONLY_EXIT_REASON
        assert fill.exit_price == close


def test_non_day_only_and_non_exact_bar_do_not_close():
    broker, position = _paper_position(strategy="orb_reclaim")
    assert resolve_paper_eod(
        broker,
        position,
        timestamp="2026-07-13T19:55:00Z",
        timeframe="5m",
        close=101.0,
    ) is None
    assert broker.get_position() is not None

    broker, position = _paper_position()
    assert resolve_paper_eod(
        broker,
        position,
        timestamp="2026-07-13T20:00:00Z",
        timeframe="5m",
        close=101.0,
    ) is None
    assert broker.get_position() is not None


def test_stop_and_target_take_precedence_over_eod_flatten():
    stop_broker, stop_position = _paper_position()
    stop_fill = stop_broker.resolve_position(NextBarOHLC(high=101.0, low=94.0))
    assert stop_fill is not None and stop_fill.exit_reason == "STOP_HIT"
    assert resolve_paper_eod(
        stop_broker,
        stop_position,
        timestamp="2026-07-13T19:55:00Z",
        timeframe="5m",
        close=98.0,
    ) is None

    target_broker, target_position = _paper_position()
    target_fill = target_broker.resolve_position(NextBarOHLC(high=111.0, low=99.0))
    assert target_fill is not None and target_fill.exit_reason == "TARGET_HIT"
    assert resolve_paper_eod(
        target_broker,
        target_position,
        timestamp="2026-07-13T19:55:00Z",
        timeframe="5m",
        close=109.0,
    ) is None


def _journal_open_position(log_dir: Path, *, strategy: str = "strat_4hr_retrigger") -> None:
    JournalLogger(str(log_dir)).log_decision(
        {
            "ts": "2026-07-13T14:00:00Z",
            "instrument": "MNQ",
            "session": "new_york",
            "decision": "TRADE",
            "setup": {
                "instrument": "MNQ",
                "direction": "LONG",
                "entry": 100.0,
                "stop": 95.0,
                "target": 110.0,
                "contracts": 1,
                "strategy": strategy,
            },
        },
        {"result": "APPROVED"},
        for_date=date(2026, 7, 13),
    )


class _FakeBroker:
    def __init__(self, position: Position | None, *, exit_price: float = 102.0):
        self.config = SimpleNamespace(env="demo")
        self.position = position
        self.exit_price = exit_price
        self.flatten_calls = 0

    def get_position_snapshot(self):
        return True, self.position

    def flatten_position(self):
        self.flatten_calls += 1
        prior = self.position
        self.position = None
        return {
            "flat_confirmed": True,
            "close_fill_price": self.exit_price,
            "close_order_id": "CLOSE-1",
            "position_was": {
                "instrument": prior.instrument,
                "direction": prior.direction,
                "qty": prior.quantity,
            },
        }


def _configure_fallback(monkeypatch, tmp_path, broker):
    monkeypatch.setattr(app_module._config, "log_dir", str(tmp_path))
    monkeypatch.setenv("BROKER", "tradovate")
    monkeypatch.setenv("TRADOVATE_ENV", "demo")
    monkeypatch.setattr(app_module, "_tv_broker", lambda: broker)


def test_fallback_no_open_is_idempotent(monkeypatch, tmp_path):
    broker = _FakeBroker(None)
    _configure_fallback(monkeypatch, tmp_path, broker)
    result = app_module._run_day_only_exit_fallback(
        now=datetime(2026, 7, 13, 20, 2, tzinfo=timezone.utc)
    )
    assert result == {"ok": True, "action": "NO_ACTION", "reason": "NO_OPEN_POSITION"}
    assert broker.flatten_calls == 0


def test_fallback_matching_demo_flattens_once_and_repeat_is_no_action(monkeypatch, tmp_path):
    _journal_open_position(tmp_path)
    broker = _FakeBroker(
        Position("MNQ", "LONG", 100.0, 95.0, 110.0, quantity=1)
    )
    _configure_fallback(monkeypatch, tmp_path, broker)
    now = datetime(2026, 7, 13, 20, 2, tzinfo=timezone.utc)

    first = app_module._run_day_only_exit_fallback(now=now)
    second = app_module._run_day_only_exit_fallback(now=now)

    assert first["action"] == "DAY_ONLY_FLATTEN"
    assert second["action"] == "NO_ACTION"
    assert broker.flatten_calls == 1
    rows = [
        json.loads(line)
        for line in (tmp_path / "journal_2026-07-13.jsonl").read_text().splitlines()
    ]
    assert rows[-1]["outcome"]["exit_reason"] == DAY_ONLY_EXIT_REASON
    assert rows[-1]["outcome"]["exit_price"] == 102.0


def test_fallback_guards_fail_closed_without_flatten(monkeypatch, tmp_path):
    cases = [
        (Position("MES", "LONG", 100.0, 95.0, 110.0, quantity=1), "INSTRUMENT_MISMATCH"),
        (Position("MNQ", "SHORT", 100.0, 105.0, 90.0, quantity=1), "DIRECTION_MISMATCH"),
        (Position("MNQ", "LONG", 100.0, 95.0, 110.0, quantity=2), "QUANTITY_MISMATCH"),
    ]
    for index, (broker_position, expected) in enumerate(cases):
        case_dir = tmp_path / str(index)
        _journal_open_position(case_dir)
        broker = _FakeBroker(broker_position)
        _configure_fallback(monkeypatch, case_dir, broker)
        result = app_module._run_day_only_exit_fallback(
            now=datetime(2026, 7, 13, 20, 2, tzinfo=timezone.utc)
        )
        assert result["reason"] == expected
        assert broker.flatten_calls == 0


def test_fallback_rejects_non_allowlisted_and_non_demo(monkeypatch, tmp_path):
    _journal_open_position(tmp_path, strategy="orb_reclaim")
    broker = _FakeBroker(Position("MNQ", "LONG", 100.0, 95.0, 110.0))
    _configure_fallback(monkeypatch, tmp_path, broker)
    now = datetime(2026, 7, 13, 20, 2, tzinfo=timezone.utc)
    assert app_module._run_day_only_exit_fallback(now=now)["reason"] == "STRATEGY_NOT_DAY_ONLY"
    assert broker.flatten_calls == 0

    other = tmp_path / "live"
    _journal_open_position(other)
    _configure_fallback(monkeypatch, other, broker)
    monkeypatch.setenv("TRADOVATE_ENV", "live")
    assert app_module._run_day_only_exit_fallback(now=now)["reason"] == "TRADOVATE_NOT_DEMO"
    assert broker.flatten_calls == 0


def test_endpoint_dedicated_secret_and_no_caller_overrides(monkeypatch):
    client = TestClient(app_module.app)
    monkeypatch.delenv("DAY_ONLY_EXIT_SECRET", raising=False)
    assert client.post("/internal/day-only-exit").status_code == 503

    monkeypatch.setenv("DAY_ONLY_EXIT_SECRET", "dedicated")
    assert client.post(
        "/internal/day-only-exit",
        headers={"X-Day-Only-Exit-Secret": "wrong"},
    ).status_code == 401

    monkeypatch.setattr(
        app_module,
        "_run_day_only_exit_fallback",
        lambda: {"ok": True, "action": "NO_ACTION", "reason": "NO_OPEN_POSITION"},
    )
    response = client.post(
        "/internal/day-only-exit",
        headers={"X-Day-Only-Exit-Secret": "dedicated"},
        json={
            "instrument": "MES",
            "strategy": "orb_reclaim",
            "close_price": 1,
            "broker_mode": "live",
        },
    )
    assert response.status_code == 200
    assert response.json()["reason"] == "NO_OPEN_POSITION"


def test_position_comparison_and_actual_fill_math():
    journal = {"instrument": "MNQ1!", "direction": "SHORT", "contracts": 2}
    broker = Position("MNQM26", "SHORT", 100.0, 105.0, 90.0, quantity=2)
    assert positions_agree(journal, broker) == (True, "MATCH")
    fill = build_day_only_fill({**journal, "entry": 100.0}, 99.0)
    assert fill.result == "WIN"
    assert fill.pnl_ticks == 4.0
    assert fill.pnl_dollars == 4.0


def test_scheduler_is_local_only_dst_aware_and_visible():
    script = Path("scripts/day_only_exit_scheduler.py").read_text()
    timer = Path("deploy/systemd/afs-day-only-exit.timer").read_text()
    service = Path("deploy/systemd/afs-day-only-exit.service").read_text()
    assert "http://127.0.0.1:8000/internal/day-only-exit" in script
    assert "TradovateBroker" not in script
    assert "DAY_ONLY_EXIT_SECRET" in script
    assert "return 1" in script
    assert "America/New_York" in timer
    assert "UTC" not in timer
    assert "EnvironmentFile=/root/afs-shared/.env" in service


def _replay_row(timestamp: str, *, close: float = 100.0) -> dict:
    row = json.loads(Path("data/replay/sample_day_mnq.jsonl").read_text().splitlines()[0])
    row.update(
        {
            "timestamp": timestamp,
            "timeframe": "5m",
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "vwap": close,
            "orb_high": close + 2.0,
            "orb_low": close - 2.0,
            "previous_day_high": close + 10.0,
            "previous_day_low": close - 10.0,
            "previous_day_close": close,
        }
    )
    return row


def _install_replay_fakes(monkeypatch):
    from risk.risk_engine import RiskResult
    from strategy.signal_engine import DecisionOutput, SetupDetail

    class FakeDecisionEngine:
        def __init__(self, config):
            pass

        def evaluate(self, state, daily_state):
            if state.timestamp.date() == date(2026, 7, 13) and state.timestamp.minute == 50:
                return DecisionOutput(
                    timestamp=state.timestamp,
                    instrument="MNQ",
                    session="new_york",
                    decision="TRADE",
                    reason="test",
                    setup=SetupDetail(
                        direction="LONG",
                        entry=100.0,
                        stop=95.0,
                        target=110.0,
                        rr_ratio=2.0,
                        strategy="strat_4hr_retrigger",
                    ),
                )
            return DecisionOutput(
                timestamp=state.timestamp,
                instrument="MNQ",
                session="new_york",
                decision="NO_TRADE",
                reason="test",
            )

    class FakeRiskEngine:
        def __init__(self, config):
            pass

        def recommended_contracts(self, instrument, balance):
            return 1

        def validate(self, setup, daily_state):
            return RiskResult("APPROVED")

    monkeypatch.setattr("replay.replay_engine.DecisionEngine", FakeDecisionEngine)
    monkeypatch.setattr("replay.replay_engine.RiskEngine", FakeRiskEngine)
    monkeypatch.setattr(
        "replay.replay_engine._score_setup",
        lambda state, setup: SimpleNamespace(
            score=0, grade="C", factors=[], penalties=[]
        ),
    )


def test_replay_uses_exact_eod_close(monkeypatch, config, tmp_path):
    from replay.replay_engine import ReplayEngine

    _install_replay_fakes(monkeypatch)
    path = tmp_path / "day.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                _replay_row("2026-07-13T19:50:00Z", close=100.0),
                _replay_row("2026-07-13T19:55:00Z", close=102.0),
                _replay_row("2026-07-13T20:00:00Z", close=999.0),
            )
        )
        + "\n"
    )
    report = ReplayEngine(config=config, log_dir=str(tmp_path / "logs")).run(path)
    assert report.wins == 1
    rows = [
        json.loads(line)
        for line in Path(report.journal_path).read_text().splitlines()
    ]
    outcome = next(row["outcome"] for row in rows if row.get("type") == "OUTCOME")
    assert outcome["exit_reason"] == DAY_ONLY_EXIT_REASON
    assert outcome["exit_price"] == 102.0


def test_run_many_missing_eod_stays_explicit_and_open(monkeypatch, config, tmp_path):
    from replay.replay_engine import ReplayEngine

    _install_replay_fakes(monkeypatch)
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    first.write_text(
        json.dumps(_replay_row("2026-07-13T19:50:00Z"))
        + "\n"
        # This later bar would hit the target, but may not substitute for the
        # missing 15:55 bar or resolve a position that should already be flat.
        + json.dumps(_replay_row("2026-07-13T20:00:00Z", close=111.0))
        + "\n"
    )
    second.write_text(json.dumps(_replay_row("2026-07-14T14:30:00Z")) + "\n")

    log_dir = tmp_path / "logs"
    report = ReplayEngine(config=config, log_dir=str(log_dir)).run_many([first, second])

    assert report.open_trades == 1
    assert report.survival_passed is False
    assert "open_trades_after_replay" in report.failure_reasons
    rows = [
        json.loads(line)
        for line in (log_dir / "journal_2026-07-13.jsonl").read_text().splitlines()
    ]
    issue = next(row for row in rows if row.get("type") == "DAY_ONLY_EXIT_ISSUE")
    assert issue["reason"] == "EOD_BAR_MISSING"
    assert not any(row.get("type") == "OUTCOME" for row in rows)
