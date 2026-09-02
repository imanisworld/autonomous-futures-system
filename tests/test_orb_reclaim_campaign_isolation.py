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


# ── Runner integration (PR #448 follow-up) ───────────────────────────────────
#
# The deployed lane runs ``enabled_concepts=["orb_breakout"]`` with
# ENTRY_REFRESH_MODE=shadow and the forward A/B campaign on. Before this
# integration the ORB Reclaim campaign arms were structurally silent because
# the runner required orb_reclaim to appear in the ACTIVE candidate audit.
# These tests use the e2e fixture with a farther close (19560) — an AUTHENTIC
# detached ORB Reclaim state under the canonical evaluator — with NO
# monkeypatching of the strategy set, so the active/isolated split is real.

import json
from datetime import date
from pathlib import Path

from execution.paper_broker import PaperBroker
from risk.risk_engine import RiskEngine
from webhook.runner import process_alert

_CAMPAIGN_FILE = "forward_ab_2026_08_v1.jsonl"
_DETACHED_CLOSE = 19560.0
_TS = "2026-05-23T15:00:00+00:00"


def _isolated_lane_cfg(tmp_path, **overrides):
    """Deployed shape: orb_breakout is the ONLY active concept."""
    return replace(
        _base_config(tmp_path),
        enabled_concepts=["orb_breakout"],
        entry_refresh_mode="shadow",
        entry_refresh_max_detachment_r=3.0,
        **overrides,
    )


def _detached_payload(**overrides):
    values = {"timestamp": _TS, "close": _DETACHED_CLOSE, "high": _DETACHED_CLOSE}
    values.update(overrides)
    return _base_payload(**values)


def _journal_rows(log_dir) -> list[dict]:
    path = next(Path(log_dir).glob("journal_*.jsonl"))
    return [json.loads(line) for line in path.read_text().splitlines()]


def _campaign_rows(log_dir) -> list[dict]:
    path = Path(log_dir) / _CAMPAIGN_FILE
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _active_decision_dict(cfg, state) -> dict:
    engine = DecisionEngine(cfg, schedule_mode=getattr(cfg, "schedule_mode", None))
    return engine.evaluate(state, _daily_state()).to_dict()


def test_authentic_orb_reclaim_is_observed_only_through_the_isolated_evaluator(tmp_path):
    """(2) A real detached ORB Reclaim state is visible to the isolated canonical
    evaluator while the active orb_breakout-only engine forms no candidate."""
    cfg = _isolated_lane_cfg(tmp_path)
    state = build_market_state(_detached_payload())

    active = _active_decision_dict(cfg, state)
    observed = observe_entry_refresh_decision(state, _daily_state(), cfg)

    assert active["decision"] != "TRADE"
    assert [c.get("strategy") for c in active["candidate_audit"]] == []
    assert "ENTRY_DETACHED_FROM_PRICE" not in (active["failed_gates"] or [])

    assert observed is not None
    assert observed.decision != "TRADE"
    assert "ENTRY_DETACHED_FROM_PRICE" in observed.failed_gates
    assert [c.get("strategy") for c in observed.candidate_audit] == ["orb_reclaim"]
    assert observed.candidate_audit[0]["reject_code"] == "ENTRY_DETACHED_FROM_PRICE"
    # (1) the active config object is untouched by observation
    assert cfg.enabled_concepts == ["orb_breakout"]


def test_runner_writes_paired_orb_reclaim_arms_from_the_isolated_observer(
    tmp_path, monkeypatch
):
    """(1)+(3) Through the runner: active config stays orb_breakout-only, the
    active decision carries no orb_reclaim, and BOTH campaign arms are written
    with one shared event_id whose context comes from the observer."""
    monkeypatch.setenv("FORWARD_EVIDENCE_CAMPAIGN", "forward_ab_2026_08_v1")
    cfg = _isolated_lane_cfg(tmp_path)
    today = date(2026, 5, 23)

    result = process_alert(_detached_payload(), config=cfg, log_dir=cfg.log_dir, for_date=today)

    assert cfg.enabled_concepts == ["orb_breakout"]
    assert result["decision"] == "NO_TRADE"
    # The active decision itself never saw orb_reclaim.
    assert "ENTRY_DETACHED_FROM_PRICE" not in (result["failed_gates"] or [])
    journal = [r for r in _journal_rows(cfg.log_dir) if r.get("decision") == "NO_TRADE"]
    assert journal, "expected the active NO_TRADE decision row"
    assert all(
        c.get("strategy") != "orb_reclaim" for c in (journal[-1].get("candidate_audit") or [])
    )
    # ... but the entry-refresh audit and the campaign arms exist.
    assert journal[-1]["entry_refresh_audit"]["strategy"] == "orb_reclaim"

    orb = [r for r in _campaign_rows(cfg.log_dir) if r["strategy"] == "orb_reclaim"]
    assert {r["variant"] for r in orb} == {"control", "modified"}
    assert len({r["event_id"] for r in orb}) == 1
    control = next(r for r in orb if r["variant"] == "control")
    modified = next(r for r in orb if r["variant"] == "modified")
    # Context comes from the ISOLATED decision (which failed on detachment),
    # not from the active decision (which had no such gate).
    assert control["failed_gates"] == ["ENTRY_DETACHED_FROM_PRICE"]
    assert control["reject_reason"]
    assert control["entry_policy"] == "current_disposition"
    assert modified["entry_policy"] == "translate_lte_1R"
    assert control["market_condition"] == modified["market_condition"] == "TRENDING"
    assert control["direction"] == modified["direction"] == "LONG"
    assert control["original_entry"] == modified["original_entry"] == 19498.5


def test_active_decision_is_identical_before_and_after_observer_execution(tmp_path, monkeypatch):
    """(4) Same fixture, active engine output byte-identical whether or not the
    observer ran (pure evaluation) and whether entry-refresh is off or shadow
    (runner-level journal row)."""
    cfg = _isolated_lane_cfg(tmp_path)
    state = build_market_state(_detached_payload())

    before = _active_decision_dict(cfg, state)
    observed = observe_entry_refresh_decision(state, _daily_state(), cfg)
    assert observed is not None and observed.candidate_audit
    after = _active_decision_dict(cfg, state)
    before.pop("ts", None)  # wall-clock evaluation stamp, not decision content
    after.pop("ts", None)
    assert before == after
    assert state.raw == build_market_state(_detached_payload()).raw

    # Runner level: the active journal fields do not depend on the observer.
    monkeypatch.setenv("FORWARD_EVIDENCE_CAMPAIGN", "forward_ab_2026_08_v1")
    today = date(2026, 5, 23)
    keys = ("decision", "reason", "failed_gates", "candidate_audit", "setup", "regime", "market_condition")
    rows = {}
    for mode in ("off", "shadow"):
        sub = tmp_path / mode
        sub.mkdir()
        cfg_mode = replace(_isolated_lane_cfg(sub), entry_refresh_mode=mode)
        process_alert(_detached_payload(), config=cfg_mode, log_dir=cfg_mode.log_dir, for_date=today)
        row = next(r for r in _journal_rows(cfg_mode.log_dir) if r.get("decision") == "NO_TRADE")
        rows[mode] = {k: row.get(k) for k in keys}
    assert rows["off"] == rows["shadow"]


def test_inverse_orb_geometry_is_unchanged_with_the_observer_active(tmp_path, monkeypatch):
    """(5) The inverse-ORB paper fixture produces the identical order with the
    observer/campaign enabled, and no orb_reclaim campaign row is written on an
    active TRADE bar (existing guard preserved)."""
    from tests.test_mnq_orb_breakout_inverse_paper import _config as _inverse_config, _payload as _inverse_payload

    monkeypatch.setenv("FORWARD_EVIDENCE_CAMPAIGN", "forward_ab_2026_08_v1")
    submitted = {}
    real_execute = PaperBroker.execute_bracket

    def spy_execute(self, order, market_price=None, **kwargs):
        submitted["order"] = order
        return real_execute(self, order, market_price=market_price, **kwargs)

    monkeypatch.setattr(PaperBroker, "execute_bracket", spy_execute)

    cfg = _inverse_config(
        tmp_path,
        mnq_orb_breakout_inverse_mode="paper_sim",
        paper_mode=False,
        stop_multiplier_per_instrument={"MNQ": 2.0},
        entry_refresh_mode="shadow",
        entry_refresh_max_detachment_r=3.0,
    )
    result = process_alert(
        _inverse_payload(timestamp=_TS), config=cfg, log_dir=cfg.log_dir, for_date=date(2026, 5, 23),
    )
    assert result["decision"] == "TRADE"
    order = submitted["order"]
    assert (order.direction, order.contracts, order.entry, order.stop, order.target) == (
        "SHORT", 1, 19498.5, 19511.0, 19471.0,
    )
    confirmed = next(r for r in _journal_rows(cfg.log_dir) if r.get("decision") == "TRADE")
    assert confirmed["setup"]["direction"] == "SHORT"
    assert confirmed["setup"]["stop"] == 19511.0
    assert confirmed["setup"]["target"] == 19471.0
    assert confirmed["setup"]["contracts"] == 1
    assert [r for r in _campaign_rows(cfg.log_dir) if r["strategy"] == "orb_reclaim"] == []


def test_observer_campaign_path_cannot_reach_risk_engine_or_broker(tmp_path, monkeypatch):
    """(6) monkeypatch-raises proof: with the isolated lane config, the ORB
    Reclaim observer + campaign writer run to completion while RiskEngine
    construction, RiskEngine.validate and broker submission all raise."""
    import webhook.runner as runner_module

    monkeypatch.setenv("FORWARD_EVIDENCE_CAMPAIGN", "forward_ab_2026_08_v1")

    class _RiskMustNotBeConstructed:
        def __init__(self, *a, **kw):
            raise AssertionError("RiskEngine must not be constructed by the ORB Reclaim observer path")

    def _validate_must_not_run(*a, **kw):
        raise AssertionError("RiskEngine.validate must not run for the ORB Reclaim observer path")

    def _broker_must_not_execute(*a, **kw):
        raise AssertionError("broker.execute_bracket must not be called by the ORB Reclaim observer path")

    monkeypatch.setattr(runner_module, "RiskEngine", _RiskMustNotBeConstructed)
    monkeypatch.setattr(RiskEngine, "validate", _validate_must_not_run)
    monkeypatch.setattr(PaperBroker, "execute_bracket", _broker_must_not_execute)

    cfg = _isolated_lane_cfg(tmp_path)
    result = process_alert(_detached_payload(), config=cfg, log_dir=cfg.log_dir, for_date=date(2026, 5, 23))

    assert result["decision"] == "NO_TRADE"
    orb = [r for r in _campaign_rows(cfg.log_dir) if r["strategy"] == "orb_reclaim"]
    assert {r["variant"] for r in orb} == {"control", "modified"}
    assert cfg.enabled_concepts == ["orb_breakout"]
