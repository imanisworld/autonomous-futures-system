"""FI-15..FI-18 — existing guards that had no direct test, plus the account pin
(#950 audit gaps 5, 7, 8 and 10).

FI-15..FI-17 are coverage: each guard already fails closed. FI-18 is regression
coverage for the fail-closed account-routing policy: automated orders require
an exact account pin, and the pinned path verifies a positive balance.
"""
from __future__ import annotations

import copy
import dataclasses
from datetime import datetime

import pytest

from execution.broker_interface import BracketOrder, Fill
from execution.tradovate_broker import TradovateBroker
from tests.fault_injection._harness import (
    ACCOUNT_ID, FakeBook, FaultRecord, make_broker, mes_payload, real_broker_cfg, run_alert,
)
from tests.fault_injection._p2_harness import (
    DAY, FOUR_HR, FakeDemoBroker, demo_cfg, demo_env, demo_payload, demo_root,
    patch_candidate, require,
)


def _mes_order() -> BracketOrder:
    return BracketOrder(
        instrument="MES", direction="LONG", entry=5900.0, stop=5896.0,
        target=5908.8, rr_ratio=2.2, strategy="orb_breakout",
    )


# ── FI-15: a restart (lost order-id registry) never re-submits the same bar ───
def test_fi15_main_runner_blocks_the_same_bar_after_restart(config, tmp_path, monkeypatch):
    book = FakeBook(place_mode="fill", children=True)
    cfg = real_broker_cfg(config, working_order_recheck=True)
    log_dir = tmp_path / "logs"
    first = run_alert(monkeypatch, make_broker(monkeypatch, book), cfg, log_dir, mes_payload())
    require(first["decision"] == "TRADE" and book.place_calls() == 1, "the bar traded once")
    TradovateBroker._reset_client_order_registry()  # process restart
    second = run_alert(monkeypatch, make_broker(monkeypatch, book), cfg, log_dir, mes_payload())
    assert second["decision"] == "BLOCKED_DUPLICATE_BAR"
    assert book.place_calls() == 1


def test_fi15_demo_lane_does_not_resubmit_a_seen_candidate_after_restart(tmp_path, monkeypatch):
    import context.wide_stop_demo_runtime as demo
    from context import wide_stop_demo_state as demo_state

    demo_env(monkeypatch)
    patch_candidate(monkeypatch, FOUR_HR)
    no_fill = Fill(
        instrument="MNQ", direction="LONG", contracts=1, entry_price=20_000.0,
        exit_price=None, exit_reason="ENTRY_NOT_FILLED", result="CANCELLED",
        pnl_ticks=None, pnl_dollars=None,
    )

    def run(broker):
        return demo.process_demo_five_min_bar(
            payload=demo_payload(), cfg=demo_cfg(), bars_5m=[], log_dir=tmp_path,
            for_date=DAY, broker_factory=lambda: broker,
        )

    first = FakeDemoBroker(fill=no_fill)
    run(first)
    state = demo_state.load_state(demo_root(tmp_path), DAY)
    require(first.execute_calls == 1 and state["pending"] is None and state["position"] is None
            and demo_state.reserved_slots(state) == 0,
            "a definitive no-fill released the slot, so only the seen key can block")
    TradovateBroker._reset_client_order_registry()  # process restart
    second = FakeDemoBroker()
    run(second)
    assert second.execute_calls == 0


# ── FI-16: live-trading guards ────────────────────────────────────────────────
def test_fi16_broker_refuses_live_env_without_the_live_flag(monkeypatch):
    book = FakeBook(place_mode="fill", children=True)
    broker = make_broker(monkeypatch, book)
    broker.config.env = "live"
    monkeypatch.delenv("LIVE_TRADING_ENABLED", raising=False)
    fill = broker.execute_bracket(_mes_order())
    assert fill.exit_reason == "LIVE_TRADING_NOT_ENABLED"
    assert book.place_calls() == 0


def test_fi16_runner_blocks_a_live_broker_when_config_is_not_live(config, tmp_path, monkeypatch):
    book = FakeBook(place_mode="fill", children=True)
    broker = make_broker(monkeypatch, book)
    broker.config.env = "live"
    cfg = real_broker_cfg(config, working_order_recheck=True)
    require(cfg.live_trading_enabled is False, "config is not live")
    result = run_alert(monkeypatch, broker, cfg, tmp_path / "logs", mes_payload())
    assert result["decision"] == "LIVE_TRADING_BLOCKED"
    assert book.place_calls() == 0


def test_fi16_yaml_live_trading_flag_is_refused(tmp_path):
    from pathlib import Path

    from config.settings import load_config

    source = Path("risk_rules.yaml").read_text()
    require(source.count("live_trading_enabled: false") == 1, "one YAML live flag to flip")
    rules = tmp_path / "risk_rules.yaml"
    rules.write_text(source.replace("live_trading_enabled: false", "live_trading_enabled: true"))
    with pytest.raises(Exception, match="live_trading_enabled"):
        load_config(str(rules))


# ── FI-17: contract cap and halted lanes ──────────────────────────────────────
def test_fi17_hard_cap_clamps_contracts(config):
    from risk.risk_engine import RiskEngine

    capped = RiskEngine(config=dataclasses.replace(config, max_contracts_hard_cap=1))
    uncapped = RiskEngine(config=dataclasses.replace(config, max_contracts_hard_cap=None))
    assert capped._cap_contracts(3) == 1
    assert uncapped._cap_contracts(3) == 3


def test_fi17_halted_daily_22_lane_refuses_entry(tmp_path, monkeypatch):
    from context import daily_22_swing_collector as lane
    from tests.test_daily_22_swing_collector import _cfg, _fixture_bars, _payload

    monkeypatch.setattr(lane, "MIN_COMPLETE_SESSION_BARS", 2)
    epoch = lane._epoch(_cfg())
    state = lane._empty_state(epoch)
    state["halted"] = True
    lane._save_state(tmp_path, state)
    bars = _fixture_bars()
    events = lane.process_five_min_bar(
        payload=_payload(datetime.fromisoformat(bars[-1]["ts"])), cfg=_cfg(),
        bars_5m=bars, log_dir=tmp_path,
    )
    assert events and events[-1]["lane_result"] == "BLOCKED"
    assert events[-1]["lane_failed_rule"] == "MAX_DRAWDOWN_HALT"
    assert lane._load_state(tmp_path, epoch)["position"] is None


def test_fi17_halted_mes_122_lane_refuses_entry(config, tmp_path, monkeypatch):
    from context import mes_122_paper_lane as lane
    from tests.test_mes_122_paper_lane import _outcome

    cfg = copy.copy(config)
    cfg.mes_122_paper_mode = "paper_sim"
    cfg.mes_122_paper_epoch_start = "2026-05-01T00:00:00+00:00"
    monkeypatch.setattr(lane, "_lane_outcomes", lambda *a, **k: [_outcome(-45.0) for _ in range(10)])
    evaluated: list = []
    monkeypatch.setattr("webhook.runner.process_alert", lambda *a, **k: evaluated.append(a) or {})
    audit = lane.observe_alert(mes_payload(), cfg=cfg, log_dir=tmp_path)
    require(audit is not None, "the lane is active for this alert")
    assert audit["lane_result"] == "HALTED_MAX_DRAWDOWN"
    assert evaluated == []


# ── FI-18: no account pin -> first account, no balance check ──────────────────
def test_fi18_unpinned_multi_account_login_refuses_to_guess(monkeypatch):
    book = FakeBook(place_mode="fill", children=True)
    broker = make_broker(monkeypatch, book, expected_account_id=None)  # pin unset
    broker._account_id = None
    monkeypatch.setattr(broker, "_resolve_account_id", lambda: setattr(
        broker, "_account_id",
        broker._select_account_id([{"id": ACCOUNT_ID}, {"id": ACCOUNT_ID + 1}]),
    ))
    fill = broker.execute_bracket(_mes_order())
    rec = FaultRecord(
        case="FI-18 unpinned login with two accounts",
        initial_journal="n/a (broker level)",
        initial_broker="two accounts visible; TRADOVATE_EXPECTED_ACCOUNT_ID unset",
        injected_failure="none — ordinary order",
        expected_safe_state="order refused; no account is guessed",
        actual_state=(f"account={broker._account_id} placeOSO={book.place_calls()} "
                      f"result={fill.result}/{fill.exit_reason}"),
    )
    assert book.place_calls() == 0, str(rec)


def test_fi18_account_pin_and_balance_are_both_fail_closed(monkeypatch):
    def zero_balance_broker(pin):
        book = FakeBook(place_mode="fill", children=True)
        book.get_faults["/cashBalance"] = [{"totalCashValue": 0.0}]
        broker = make_broker(monkeypatch, book, expected_account_id=pin)
        return book, broker

    pinned_book, pinned = zero_balance_broker(ACCOUNT_ID)
    pinned_fill = pinned.execute_bracket(_mes_order())
    require(
        pinned_fill.exit_reason == "ACCOUNT_NONPOSITIVE_BALANCE"
        and pinned_book.place_calls() == 0,
        "a pinned account still requires a verified positive balance",
    )
    book, unpinned = zero_balance_broker(None)
    fill = unpinned.execute_bracket(_mes_order())
    rec = FaultRecord(
        case="FI-18 unpinned account with zero balance",
        initial_journal="n/a (broker level)",
        initial_broker="one account, cash balance 0; pin unset",
        injected_failure="none — ordinary order",
        expected_safe_state="refused as ACCOUNT_PIN_REQUIRED before order submission",
        actual_state=f"placeOSO={book.place_calls()} result={fill.result}/{fill.exit_reason}",
    )
    assert fill.exit_reason == "ACCOUNT_PIN_REQUIRED", str(rec)
    assert book.place_calls() == 0, str(rec)
