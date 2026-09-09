"""Regression coverage for the canonical wide-stop forward collector.

The collector must create usable forward evidence without re-enabling 4HR or
3-2-2 in the active strategy book and without introducing any external-broker
route.
"""
from __future__ import annotations

import copy
from datetime import date, datetime, timezone
from types import SimpleNamespace

from config.settings import load_config
from context import wide_stop_ledger_paper as contract
from context.wide_stop_forward_collector import (
    FOUR_HR,
    MAX_FILLED_PER_DAY,
    _empty_state,
    _isolated_config,
    _load_state,
    _resolve_one_position,
    _save_state,
    process_five_min_bar,
)
from context.wide_stop_ledger_runtime import observe_candidate
from risk.risk_engine import RiskResult, TradeSetup

EPOCH = "2026-09-08T00:00:00+00:00"
DAY = date(2026, 9, 8)


def _cfg():
    cfg = copy.copy(load_config())
    cfg.wide_stop_ledger_mode = "paper_sim"
    cfg.wide_stop_ledger_epoch_start = EPOCH
    cfg.max_stop_ticks = {"MNQ": 120.0}
    cfg.min_rr_ratio = 2.0
    cfg.max_daily_loss = 150.0
    cfg.max_drawdown_percent = 0.20
    cfg.runner_mode = False
    cfg.schedule_mode = "always_on_paper"
    return cfg


def _payload(ts="2026-09-08T14:05:00+00:00", *, high=20_011.0, low=20_000.0, close=20_008.0):
    return SimpleNamespace(
        ticker="MNQ1!",
        timestamp=ts,
        timeframe="5m",
        open=20_001.0,
        high=high,
        low=low,
        close=close,
        volume=1000,
    )


def _setup():
    return TradeSetup(
        direction="LONG",
        entry=20_000.0,
        stop=19_936.5,  # 254 ticks: rejected globally, admitted by 300t lane
        target=20_088.9,
        rr_ratio=1.4,
        strategy=FOUR_HR,
        instrument="MNQ",
        session="new_york",
        contracts=1,
        entry_time=datetime(2026, 9, 8, 14, 0, tzinfo=timezone.utc),
        confluence_grade="B",
    )


def _position(*, stop=19_990.0, target=20_010.0):
    return {
        "candidate_key": "fourhr|2026-09-08T14:00:00+00:00",
        "strategy": FOUR_HR,
        "instrument": "MNQ",
        "session": "new_york",
        "direction": "LONG",
        "planned_entry": 20_000.0,
        "entry": 20_000.25,
        "stop": stop,
        "target": target,
        "rr_ratio": 1.0,
        "contracts": 1,
        "entry_time": "2026-09-08T14:00:00+00:00",
        "paper_order_id": "PAPER-test-wide-stop",
        "trading_date": DAY.isoformat(),
    }


def test_isolated_config_never_reenables_strategy_in_active_book():
    cfg = _cfg()
    cfg.enabled_concepts = ["orb_breakout"]
    cfg.strategy_status = {"orb_breakout": "SHADOW_ONLY"}
    cfg.disabled_concepts_per_instrument = {
        "MNQ": ["orb_breakout", FOUR_HR],
        "MES": [FOUR_HR],
    }
    before_enabled = list(cfg.enabled_concepts)
    before_status = dict(cfg.strategy_status)
    before_disabled = copy.deepcopy(cfg.disabled_concepts_per_instrument)
    before_stop = dict(cfg.max_stop_ticks)
    before_rr = cfg.min_rr_ratio

    isolated = _isolated_config(cfg, contract.LEDGERS["wide_stop_4k"], FOUR_HR)

    assert cfg.enabled_concepts == before_enabled
    assert cfg.strategy_status == before_status
    assert cfg.disabled_concepts_per_instrument == before_disabled
    assert cfg.max_stop_ticks == before_stop
    assert cfg.min_rr_ratio == before_rr
    assert isolated.enabled_concepts == [FOUR_HR]
    assert isolated.strategy_status[FOUR_HR] == "PAPER_ELIGIBLE"
    assert FOUR_HR not in isolated.disabled_concepts_per_instrument["MNQ"]
    assert isolated.max_stop_ticks["MNQ"] == 300.0
    assert isolated.min_rr_ratio == 1.0


def test_entry_audit_persists_candidate_identity_and_paper_order_id(tmp_path):
    audit = observe_candidate(
        cfg=_cfg(),
        setup=_setup(),
        global_risk_result=RiskResult(
            result="REJECTED", failed_rule="stop_too_wide", reason="wide"
        ),
        log_dir=str(tmp_path),
        for_date=DAY,
        market_price=20_000.0,
        schedule_mode="always_on_paper",
        candidate_key="candidate-123",
    )
    assert audit is not None
    assert audit["candidate_key"] == "candidate-123"
    assert audit["fill_status"] == "OPEN"
    assert str(audit["fill_paper_order_id"]).startswith("PAPER-")


def test_open_position_resolves_through_real_paperbroker_and_charges_commission(tmp_path):
    cfg = _cfg()
    ledger = contract.LEDGERS["wide_stop_4k"]
    state = _empty_state()
    state["position"] = _position()
    _save_state(tmp_path, ledger, state)

    outcome = _resolve_one_position(
        cfg=cfg,
        ledger=ledger,
        log_dir=tmp_path,
        for_date=DAY,
        current_ts=datetime(2026, 9, 8, 14, 5, tzinfo=timezone.utc),
        bar={"high": 20_011.0, "low": 20_000.0, "close": 20_008.0},
    )

    assert outcome is not None
    assert outcome["collector_event"] == "OUTCOME"
    assert outcome["outcome_result"] == "WIN"
    assert outcome["exit_reason"] == "TARGET_HIT"
    assert outcome["valid_outcome"] is True
    assert outcome["net_pnl_dollars"] == round(
        outcome["gross_pnl_dollars"] - contract.COMMISSION_ROUND_TRIP, 2
    )
    assert _load_state(tmp_path, ledger)["position"] is None


def test_same_bar_stop_and_target_resolves_pessimistically_as_loss(tmp_path):
    cfg = _cfg()
    ledger = contract.LEDGERS["wide_stop_4k"]
    state = _empty_state()
    state["position"] = _position(stop=19_990.0, target=20_010.0)
    _save_state(tmp_path, ledger, state)

    outcome = _resolve_one_position(
        cfg=cfg,
        ledger=ledger,
        log_dir=tmp_path,
        for_date=DAY,
        current_ts=datetime(2026, 9, 8, 14, 5, tzinfo=timezone.utc),
        bar={"high": 20_011.0, "low": 19_989.0, "close": 20_000.0},
    )

    assert outcome is not None
    assert outcome["outcome_result"] == "LOSS"
    assert outcome["exit_reason"] == "STOP_HIT"
    assert outcome["gross_pnl_dollars"] < 0


def test_missing_1555_eod_bar_fails_closed_without_inventing_pnl(tmp_path):
    cfg = _cfg()
    ledger = contract.LEDGERS["wide_stop_4k"]
    state = _empty_state()
    state["position"] = _position()
    _save_state(tmp_path, ledger, state)

    outcome = _resolve_one_position(
        cfg=cfg,
        ledger=ledger,
        log_dir=tmp_path,
        for_date=DAY,
        current_ts=datetime(2026, 9, 8, 20, 0, tzinfo=timezone.utc),  # 16:00 ET
        bar={"high": 20_005.0, "low": 19_995.0, "close": 20_001.0},
    )

    assert outcome is not None
    assert outcome["lane_result"] == "UNRESOLVED_EOD_MISSING"
    assert outcome["outcome_result"] == "UNRESOLVED"
    assert outcome["valid_outcome"] is False
    assert outcome["net_pnl_dollars"] is None
    assert _load_state(tmp_path, ledger)["position"] is None


def test_max_three_filled_trades_per_day_blocks_fourth_candidate(tmp_path, monkeypatch):
    cfg = _cfg()
    ledger = contract.LEDGERS["wide_stop_4k"]
    state = _empty_state()
    state["filled_date"] = DAY.isoformat()
    state["filled_count"] = MAX_FILLED_PER_DAY
    _save_state(tmp_path, ledger, state)

    candidate = {
        "direction": "LONG",
        "entry": 20_000.0,
        "stop": 19_950.0,
        "target": 20_070.0,
        "entry_time": datetime(2026, 9, 8, 14, 5, tzinfo=timezone.utc),
    }
    decision = SimpleNamespace(decision="TRADE", setup=object(), failed_gates=[], reason="ok")

    import context.wide_stop_forward_collector as collector

    def fake_eval(**kwargs):
        if kwargs["strategy"] == FOUR_HR:
            return decision, object(), candidate
        return None, None, None

    monkeypatch.setattr(collector, "_evaluate_canonical_candidate", fake_eval)

    events = process_five_min_bar(
        payload=_payload(),
        cfg=cfg,
        bars_5m=[],
        log_dir=tmp_path,
        for_date=DAY,
    )

    blocked = [event for event in events if event.get("lane_result") == "BLOCKED_MAX_TRADES"]
    assert len(blocked) == 1
    assert blocked[0]["lane_failed_rule"] == "max_trades_per_day"
    assert _load_state(tmp_path, ledger)["filled_count"] == MAX_FILLED_PER_DAY
    assert _load_state(tmp_path, ledger)["position"] is None
