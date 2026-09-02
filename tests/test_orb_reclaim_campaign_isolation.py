"""Regression proof for ORB Reclaim campaign observation under inverse-ORB isolation.

The active deployed lane intentionally narrows ``enabled_concepts`` to
``["orb_breakout"]``. Entry-refresh / forward-campaign observation must still be
able to run the canonical ORB Reclaim evaluator without adding ORB Reclaim back
to active strategy ranking.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from context.mnq_entry_refresh import observe_entry_refresh_decision
from risk.risk_engine import DailyState
from strategy.signal_engine import DecisionEngine, SetupDetail
from tests.test_e2e_scenarios import _base_config, _base_payload
from webhook.state_builder import build_market_state


def _daily_state() -> DailyState:
    return DailyState(
        trade_count=0,
        consecutive_losses=0,
        has_open_position=False,
        realized_pnl_dollars=0.0,
        orb_break_long_played={"MNQ": True},
        orb_break_short_played={},
    )


def _detached_orb_reclaim() -> SetupDetail:
    return SetupDetail(
        direction="LONG",
        entry=19479.25,
        stop=19469.25,
        target=19504.25,
        rr_ratio=2.5,
        strategy="orb_reclaim",
    )


def test_observer_is_off_when_entry_refresh_is_off(tmp_path):
    cfg = replace(_base_config(tmp_path), enabled_concepts=["orb_breakout"])
    state = build_market_state(_base_payload(timestamp="2026-05-23T15:00:00+00:00"))

    assert observe_entry_refresh_decision(state, _daily_state(), cfg) is None
    assert cfg.enabled_concepts == ["orb_breakout"]


def test_observer_runs_orb_reclaim_without_reenabling_it_in_active_config(
    tmp_path, monkeypatch
):
    cfg = replace(
        _base_config(tmp_path),
        enabled_concepts=["orb_breakout"],
        entry_refresh_mode="shadow",
    )
    state = build_market_state(_base_payload(timestamp="2026-05-23T15:00:00+00:00"))
    setup = _detached_orb_reclaim()
    seen_enabled: list[tuple[str, ...]] = []

    def _scoped_candidates(self, *args, **kwargs):
        seen_enabled.append(tuple(self.config.enabled_concepts))
        return [setup]

    monkeypatch.setattr(DecisionEngine, "_find_setup_candidates", _scoped_candidates)

    decision = observe_entry_refresh_decision(state, _daily_state(), cfg)

    assert decision is not None
    assert "ENTRY_DETACHED_FROM_PRICE" in (decision.failed_gates or [])
    assert decision.candidate_audit
    assert decision.candidate_audit[0]["strategy"] == "orb_reclaim"
    assert decision.candidate_audit[0]["reject_code"] == "ENTRY_DETACHED_FROM_PRICE"
    assert seen_enabled
    assert set(seen_enabled) == {("orb_reclaim",)}
    # Load-bearing safety proof: the active config object remains isolated.
    assert cfg.enabled_concepts == ["orb_breakout"]


def test_observer_uses_throwaway_daily_state_including_nested_dicts(
    tmp_path, monkeypatch
):
    cfg = replace(
        _base_config(tmp_path),
        enabled_concepts=["orb_breakout"],
        entry_refresh_mode="shadow",
    )
    state = build_market_state(_base_payload(timestamp="2026-05-23T15:00:00+00:00"))
    live_daily = _daily_state()

    sentinel = SimpleNamespace(decision="NO_TRADE", failed_gates=[], candidate_audit=[])

    def _mutating_evaluate(self, observed_state, scoped_daily):
        assert self.config.enabled_concepts == ["orb_reclaim"]
        scoped_daily.trade_count = 99
        scoped_daily.orb_break_long_played["MNQ"] = False
        scoped_daily.orb_break_short_played["MNQ"] = True
        return sentinel

    monkeypatch.setattr(DecisionEngine, "evaluate", _mutating_evaluate)

    result = observe_entry_refresh_decision(state, live_daily, cfg)

    assert result is sentinel
    assert live_daily.trade_count == 0
    assert live_daily.orb_break_long_played == {"MNQ": True}
    assert live_daily.orb_break_short_played == {}
    assert cfg.enabled_concepts == ["orb_breakout"]


def test_observer_uses_throwaway_market_state_including_nested_raw(
    tmp_path, monkeypatch
):
    cfg = replace(
        _base_config(tmp_path),
        enabled_concepts=["orb_breakout"],
        entry_refresh_mode="shadow",
    )
    state = build_market_state(_base_payload(timestamp="2026-05-23T15:00:00+00:00"))
    state.raw["observer_isolation_sentinel"] = "live"
    original_instrument = state.instrument

    sentinel = SimpleNamespace(decision="NO_TRADE", failed_gates=[], candidate_audit=[])

    def _mutating_evaluate(self, observed_state, scoped_daily):
        assert observed_state is not state
        observed_state.instrument = "MUTATED"
        observed_state.raw["observer_isolation_sentinel"] = "scoped"
        return sentinel

    monkeypatch.setattr(DecisionEngine, "evaluate", _mutating_evaluate)

    result = observe_entry_refresh_decision(state, _daily_state(), cfg)

    assert result is sentinel
    assert state.instrument == original_instrument
    assert state.raw["observer_isolation_sentinel"] == "live"
    assert cfg.enabled_concepts == ["orb_breakout"]


def test_observer_rejects_out_of_scope_instrument(tmp_path):
    cfg = replace(
        _base_config(tmp_path),
        enabled_concepts=["orb_breakout"],
        entry_refresh_mode="shadow",
    )
    state = build_market_state(_base_payload(timestamp="2026-05-23T15:00:00+00:00"))
    state.instrument = "MES"

    assert observe_entry_refresh_decision(state, _daily_state(), cfg) is None
    assert cfg.enabled_concepts == ["orb_breakout"]
