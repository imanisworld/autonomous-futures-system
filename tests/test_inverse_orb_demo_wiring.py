from dataclasses import replace
from datetime import date
from unittest.mock import Mock

import pytest

from config.settings import ConfigError, _validate_config
from execution.broker_interface import Fill
from execution.inverse_demo_safety import assert_account_clear, reserve_submit
from execution.tradovate_broker import TradovateBroker, TradovateConfig
from journal.journal_logger import JournalLogger
from scripts.inverse_orb_demo_parity import build_report, capture_demo_payload
from tests.test_inverse_orb_tradovate_demo_contract import _source_order
from context.mnq_orb_breakout_inverse_paper import mirror_order
from tests.test_mnq_orb_breakout_inverse_paper import _config, _payload
from webhook.runner import _inverse_accounting_epoch_start, _inverse_demo_broker, process_alert


@pytest.fixture
def demo_env(monkeypatch):
    for name, value in {
        "BROKER": "tradovate", "TRADOVATE_ENV": "demo",
        "LIVE_TRADING_ENABLED": "false", "TRADOVATE_EXPECTED_ACCOUNT_ID": "999",
        "DISCORD_NOTIFICATIONS_ENABLED": "false",
    }.items():
        monkeypatch.setenv(name, value)


def demo_config(tmp_path, **overrides):
    return _config(tmp_path, mnq_orb_breakout_inverse_mode="tradovate_demo",
                   paper_mode=False, max_staleness_seconds=60, **overrides)


def demo_broker(monkeypatch):
    broker = TradovateBroker(TradovateConfig(env="demo", expected_account_id=999))
    broker._account_id = 999
    monkeypatch.setattr(broker, "_authenticate", lambda: True)
    monkeypatch.setattr(broker, "get_account_balance", lambda: 5000.0)
    monkeypatch.setattr(broker, "_get", lambda path: [])
    monkeypatch.setattr("webhook.runner._make_broker", lambda **kwargs: broker)
    return broker


def test_demo_config_and_epoch(tmp_path, demo_env):
    cfg = demo_config(tmp_path)
    _validate_config(cfg)
    assert _inverse_accounting_epoch_start(cfg).isoformat() == cfg.mnq_orb_breakout_inverse_epoch_start


@pytest.mark.parametrize("name,value", [
    ("BROKER", "paper"), ("TRADOVATE_ENV", "live"), ("TRADOVATE_ENV", ""),
    ("LIVE_TRADING_ENABLED", "true"), ("LIVE_TRADING_ENABLED", ""),
    ("TRADOVATE_EXPECTED_ACCOUNT_ID", ""), ("TRADOVATE_EXPECTED_ACCOUNT_ID", "abc"),
    ("TRADOVATE_EXPECTED_ACCOUNT_ID", "0"),
])
def test_demo_config_rejects_unsafe_route(tmp_path, demo_env, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError):
        _validate_config(demo_config(tmp_path))


@pytest.mark.parametrize("change", [
    {"mnq_orb_breakout_inverse_epoch_start": None},
    {"mnq_orb_breakout_inverse_epoch_start": "2026-09-08T00:00:00"},
    {"mnq_orb_breakout_proof_mode": "paper_sim"},
    {"paper_mode": True}, {"working_order_recheck_enabled": False},
])
def test_demo_config_preserves_constraints(tmp_path, demo_env, change):
    with pytest.raises(ConfigError):
        _validate_config(replace(demo_config(tmp_path), **change))


@pytest.mark.parametrize("replacement", [None, TradovateConfig(env="live", expected_account_id=999),
                                         TradovateConfig(env="demo", expected_account_id=998)])
def test_factory_cannot_fall_back_or_change_account(tmp_path, demo_env, monkeypatch, replacement):
    broker = TradovateBroker(replacement) if replacement else object()
    monkeypatch.setattr("webhook.runner._make_broker", lambda **kwargs: broker)
    with pytest.raises(ConfigError):
        _inverse_demo_broker(demo_config(tmp_path), 5000)


def test_canonical_63_arm_payload_parity():
    report = build_report()
    assert report["baseline_file_sha256"] == "033e6a4d186b3fe9862ac219bfa359486cd6e9166f4c5d229d22d1dce4f149ba"
    assert (report["arms"], report["fills"], report["no_fills"]) == (63, 57, 6)
    assert report["entry_mismatches"] == report["bracket_mismatches"] == 0
    assert report["legacy_rr_clamp_fill_differences"] == 1
    assert report["post_fill_rejected_fills"] == 38
    assert report["verdict"] == "HOLD"


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_exact_eight_ticks_and_static_children_override_global_modes(direction):
    source = _source_order(post_fill_validation_required=True)
    if direction == "LONG":
        source = replace(source, direction="SHORT", stop=25012.0, target=24976.0)
    inverse = mirror_order(source)
    payload = capture_demo_payload(inverse)
    assert payload["price"] == inverse.entry + (2 if direction == "LONG" else -2)
    assert payload["timeInForce"] == "IOC"
    assert payload["orderQty"] == 1
    assert payload["bracket1"]["price"] == inverse.target
    assert payload["bracket2"]["stopPrice"] == inverse.stop


@pytest.mark.parametrize("response", [None, {}, [None], [{"ordStatus": "Working"}],
                                       [{"ordStatus": "PendingCancel"}], [{}]])
def test_unreadable_or_working_orders_block(demo_env, monkeypatch, response):
    broker = demo_broker(monkeypatch)
    monkeypatch.setattr(broker, "_get", lambda path: response if path == "/order/list" else [])
    with pytest.raises(ValueError):
        assert_account_clear(broker)


@pytest.mark.parametrize("response", [None, {}, [None], [{}], [{"netPos": 1}], [{"netPos": "NaN"}]])
def test_unreadable_or_open_positions_block(demo_env, monkeypatch, response):
    broker = demo_broker(monkeypatch)
    monkeypatch.setattr(broker, "_get", lambda path: response if path == "/position/list" else [])
    with pytest.raises(ValueError):
        assert_account_clear(broker)


def test_pending_submit_survives_broker_restart(tmp_path, demo_env, monkeypatch):
    broker = demo_broker(monkeypatch)
    latch = reserve_submit(tmp_path, broker, "attempt-1")
    assert latch.exists()
    restarted = demo_broker(monkeypatch)
    with pytest.raises(ValueError, match="unresolved_prior_submit"):
        reserve_submit(tmp_path, restarted, "attempt-2")
    assert latch.exists()


def test_wrong_resolved_account_blocks_before_order(tmp_path, demo_env, monkeypatch):
    broker = demo_broker(monkeypatch)
    broker._account_id = 998
    with pytest.raises(ValueError, match="ACCOUNT_MISMATCH"):
        reserve_submit(tmp_path, broker, "attempt")
    assert not (tmp_path / "inverse_demo_submit_pending.json").exists()


def test_runner_demo_submission_and_later_resolution(tmp_path, demo_env, monkeypatch):
    cfg = replace(demo_config(tmp_path, exit_mode="runner_live"), max_staleness_seconds=0)
    monkeypatch.setenv("EXIT_MODE", "runner_live")
    broker = demo_broker(monkeypatch)
    submitted = []

    def execute(order):  # Deliberately accepts no PaperBroker-only kwargs.
        submitted.append(order)
        broker._last_order_ids = {"entry": 101, "stop": 102, "target": 103}
        return Fill(order.instrument, order.direction, 1, order.entry, None, None, "OPEN", None, None)

    monkeypatch.setattr(broker, "execute_bracket", execute)
    today = date(2026, 5, 23)
    result = process_alert(_payload(timestamp="2026-05-23T15:00:00+00:00"),
                           config=cfg, log_dir=cfg.log_dir, for_date=today)
    assert result["decision"] == "TRADE"
    order = submitted[0]
    assert order.direction == "SHORT"
    assert order.post_fill_validation_required is True
    assert order.max_slippage_ticks == 8
    position = JournalLogger(log_dir=cfg.log_dir).get_open_position(today)
    assert position["stop"] == order.stop
    assert position["target"] == order.target
    assert position["exit_mode"] == "static"
    from pathlib import Path
    assert not (Path(cfg.log_dir) / "inverse_demo_submit_pending.json").exists()
    resolver = Mock(return_value=None)
    monkeypatch.setattr(broker, "resolve_position", resolver)
    monkeypatch.setattr(broker, "replace_stop", Mock(side_effect=AssertionError("static exits")))
    process_alert(_payload(timestamp="2026-05-23T15:15:00+00:00"),
                  config=cfg, log_dir=cfg.log_dir, for_date=today)
    resolver.assert_called_once()
    assert broker._last_order_ids == {"entry": 101, "stop": 102, "target": 103}
    assert len(submitted) == 1


@pytest.mark.parametrize("positions", [{}, None, [{}], [{"netPos": "NaN"}], []])
def test_inverse_resolution_cannot_invent_exit_from_old_fill(demo_env, monkeypatch, positions):
    from execution.broker_interface import Position
    broker = demo_broker(monkeypatch)
    broker._last_position = Position("MNQ", "LONG", 25000, 24988, 25024)
    broker._last_order_ids = {"instrument": "MNQ", "entry": 101, "stop": 102, "target": 103, "contract_id": 123}
    monkeypatch.setattr(broker, "_get", lambda path: positions if path == "/position/list" else [
        {"contractId": 123, "orderId": 888, "price": 25024, "qty": 1},
    ])
    for _ in range(4):
        assert broker.resolve_position(strict_order_identity=True) is None
    assert broker._last_position.open


def test_inverse_resolution_uses_recorded_contract_and_exact_child(demo_env, monkeypatch):
    from execution.broker_interface import Position
    broker = demo_broker(monkeypatch)
    broker._last_position = Position("MNQ", "LONG", 25000, 24988, 25024)
    broker._last_order_ids = {"instrument": "MNQ", "entry": 101, "stop": 102, "target": 103, "contract_id": 123}
    monkeypatch.setattr(broker, "_find_contract_id", Mock(side_effect=AssertionError("must retain entry contract across roll")))
    monkeypatch.setattr(broker, "_get", lambda path: [] if path == "/position/list" else [
        {"contractId": 123, "orderId": 888, "price": 24988, "qty": 1},
        {"contractId": 123, "orderId": 103, "price": 25024, "qty": 1},
    ])
    fill = broker.resolve_position(strict_order_identity=True)
    assert fill.exit_reason == "TARGET_HIT"
    assert fill.pnl_dollars == 48


@pytest.mark.parametrize("initial,final", [("working", "unknown"), ("unknown", "working"),
                                           ("working", "filled"), ("unknown", "dead")])
def test_inverse_cancel_race_preserves_children(demo_env, monkeypatch, initial, final):
    broker = demo_broker(monkeypatch)
    monkeypatch.setattr("execution.tradovate_supervisor.tradovate_order_ready", lambda: True)
    monkeypatch.setattr(broker, "_find_contract_id", lambda instrument: 123)
    calls = []

    def post(path, body):
        calls.append((path, body))
        if path == "/order/placeOSO":
            return {"orderId": 101, "oso1Id": 103, "oso2Id": 102}
        return {}

    monkeypatch.setattr(broker, "_post", post)
    monkeypatch.setattr(broker, "_entry_status", Mock(side_effect=[initial, final]))
    monkeypatch.setattr(broker, "_entry_fill_price", lambda *args: 25000)
    monkeypatch.setattr(broker, "_verify_bracket_children", lambda *args, **kwargs: (True, True))
    order = mirror_order(_source_order(post_fill_validation_required=True))
    fill = broker.execute_bracket(order)
    cancelled = [body["orderId"] for path, body in calls if path == "/order/cancelorder"]
    assert cancelled == [101]
    if final == "filled":
        assert fill.result == "OPEN"
    elif final == "dead":
        assert fill.exit_reason == "ENTRY_NOT_FILLED"
        assert broker._inverse_submit_uncertain is False
    else:
        assert fill.exit_reason == "ENTRY_UNCONFIRMED"
        assert broker._inverse_submit_uncertain is True


@pytest.mark.parametrize("env", ["live", "unknown"])
def test_inverse_broker_never_submits_to_other_environment(demo_env, monkeypatch, env):
    broker = demo_broker(monkeypatch)
    broker.config.env = env
    post = Mock(side_effect=AssertionError("must never submit"))
    monkeypatch.setattr(broker, "_post", post)
    fill = broker.execute_bracket(mirror_order(_source_order(post_fill_validation_required=True)))
    assert fill.exit_reason == "INVERSE_DEMO_ROUTE_INVALID"
    post.assert_not_called()


@pytest.mark.parametrize("change", [{"contracts": 2}, {"max_slippage_ticks": 32},
                                    {"post_fill_validation_required": False},
                                    {"stop": float("nan")}, {"target": 24976.1},
                                    {"force_runner_exit": True}])
def test_invalid_inverse_order_fails_before_post(demo_env, monkeypatch, change):
    broker = demo_broker(monkeypatch)
    post = Mock(side_effect=AssertionError("must never submit"))
    monkeypatch.setattr(broker, "_post", post)
    order = replace(mirror_order(_source_order(post_fill_validation_required=True)), **change)
    assert broker.execute_bracket(order).exit_reason == "INVERSE_IOC_CONTRACT_INVALID"
    post.assert_not_called()


def test_failed_journal_confirmation_retains_latch(tmp_path, demo_env, monkeypatch):
    from execution.inverse_demo_safety import release_journaled_submit
    broker = demo_broker(monkeypatch)
    latch = reserve_submit(tmp_path, broker, "attempt")
    journal = JournalLogger(log_dir=tmp_path)
    with pytest.raises(ValueError, match="journal_confirmation_missing"):
        release_journaled_submit(latch, journal, date(2026, 5, 23), "attempt", {"entry": 1})
    assert latch.exists()


def test_demo_simulate_cannot_contact_broker(tmp_path, demo_env, monkeypatch):
    cfg = replace(demo_config(tmp_path), max_staleness_seconds=0, paper_mode=True)
    factory = Mock(side_effect=AssertionError("simulate must not create external broker"))
    monkeypatch.setattr("webhook.runner._make_broker", factory)
    with pytest.raises(ConfigError, match="simulate"):
        process_alert(_payload(timestamp="2026-05-23T15:00:00+00:00"), config=cfg,
                      log_dir=cfg.log_dir, for_date=date(2026, 5, 23))
    factory.assert_not_called()


@pytest.mark.parametrize("response", [{"failureReason": "rejected"}, {}, None, {"orderId": 201}])
def test_inverse_liquidation_never_cancels_protection_before_flat(demo_env, monkeypatch, response):
    from execution.broker_interface import Position
    broker = demo_broker(monkeypatch)
    broker._last_position = Position("MNQ", "LONG", 25000, 24988, 25024)
    broker._last_order_ids = {"instrument": "MNQ", "entry": 101, "stop": 102, "target": 103,
                              "contract_id": 123, "inverse_demo": True}
    monkeypatch.setattr(broker, "_find_contract_id", Mock(side_effect=AssertionError("must not roll contract")))
    monkeypatch.setattr(broker, "_get", lambda path: [{"accountId": 999, "contractId": 123, "netPos": 1}])
    monkeypatch.setattr("execution.inverse_demo_safety.time.sleep", lambda _: None)
    post = Mock(return_value=response)
    cancel = Mock(side_effect=AssertionError("must keep children"))
    monkeypatch.setattr(broker, "_post", post)
    monkeypatch.setattr(broker, "_cancel_oso", cancel)
    result = broker.flatten_position()
    assert result["flat_confirmed"] is False
    assert result["cancelled_orders"] is False
    assert broker._last_position.open
    assert post.call_args.args[1]["contractId"] == 123
    cancel.assert_not_called()


def test_inverse_liquidation_cancels_own_children_only_after_flat(demo_env, monkeypatch):
    from execution.broker_interface import Position
    broker = demo_broker(monkeypatch)
    broker._last_position = Position("MNQ", "LONG", 25000, 24988, 25024)
    broker._last_order_ids = {"instrument": "MNQ", "entry": 101, "stop": 102, "target": 103,
                              "contract_id": 123, "inverse_demo": True}
    events = []
    reads = iter([[{"accountId": 999, "contractId": 123, "netPos": 1}], []])

    def get(path):
        row = next(reads)
        events.append("open" if row else "flat")
        return row

    monkeypatch.setattr(broker, "_get", get)
    monkeypatch.setattr(broker, "_post", lambda path, body: {"orderId": 201})
    monkeypatch.setattr(broker, "_entry_fill_price", lambda *args: 25000.25)
    def cancel(*ids):
        assert ids == (103, 102)
        events.append("cancel")
        return 2
    monkeypatch.setattr(broker, "_cancel_oso", cancel)
    result = broker.flatten_position()
    assert events == ["open", "flat", "cancel"]
    assert result["flat_confirmed"] is True
    assert result["close_fill_price"] == 25000.25
