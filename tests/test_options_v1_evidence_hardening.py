from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from alert_ranker.causal_bars import Bar, MINUTE_30
from alert_ranker.contract_marks import aggregate_open_planned_risk
from alert_ranker.scorer import ScoreResult
from alert_ranker.storage import ScanStorage
from alert_ranker.v1_evidence_hardening import (
    COUNTERFACTUAL_LANE,
    TIMEFRAME_1H,
    _observer_candidate,
    _timeframe_series,
    build_v1_evidence_hardening,
)
from alert_ranker.v1_runtime_preflight import build_v1_runtime_preflight

UTC = timezone.utc


def bar(hour: int, minute: int, *, high: float, low: float, open_: float | None = None, close: float | None = None) -> Bar:
    return Bar(
        start=datetime(2026, 9, 8, hour, minute, tzinfo=UTC),
        open=open_ if open_ is not None else (high + low) / 2,
        high=high,
        low=low,
        close=close if close is not None else (high + low) / 2,
        volume=1000,
    )


def pin_v1_env(monkeypatch) -> None:
    monkeypatch.setenv("OPTIONS_PAPER_V1_COLLECTION_ENABLED", "true")
    monkeypatch.setenv("OPTIONS_MANAGER_MAX_AGGREGATE_OPEN_RISK_DOLLARS", "1000")
    monkeypatch.setenv("OPTIONS_MANAGER_RISK_MIN_DTE_DAYS", "14")
    monkeypatch.setenv("OPTIONS_COMPANION_ENABLED", "false")


def test_observer_candidate_preserves_mechanical_212_without_promoting_trade():
    prior = [
        bar(13, 30, high=10.0, low=8.0),
        bar(14, 0, high=11.0, low=8.5),
        bar(14, 30, high=10.5, low=9.0),
    ]
    current = bar(15, 0, high=11.2, low=9.2)
    candidate = _observer_candidate(
        prior=prior,
        current=current,
        timeframe=TIMEFRAME_1H,
        prefix="H1_",
        filter_reason="timeframe_observation_only",
    )
    assert candidate is not None
    assert candidate["setup_type"] == "H1_212_CONTINUATION"
    assert candidate["setup_timeframe"] == "1H"
    assert candidate["mechanical_signal_status"] == "TRIGGERED"
    assert candidate["setup_status"] == "OBSERVE"
    assert candidate["paper_evidence_lane"] == COUNTERFACTUAL_LANE
    assert candidate["counterfactual_observer"] is True


def test_session_timeframe_builder_keeps_completed_groups_and_current_partial():
    session = SimpleNamespace(
        date=datetime(2026, 9, 8, tzinfo=UTC).date(),
        open=datetime(2026, 9, 8, 13, 30, tzinfo=UTC),
        close=datetime(2026, 9, 8, 20, 0, tzinfo=UTC),
    )
    bars = [
        bar(13, 30, high=10.0, low=9.0),
        bar(14, 0, high=10.2, low=9.2),
        bar(14, 30, high=10.4, low=9.4),
    ]
    prior, current = _timeframe_series(
        bars=bars,
        source_timeframe=MINUTE_30,
        sessions=[session],
        current_session=session,
        cutoff=datetime(2026, 9, 8, 15, 5, tzinfo=UTC),
        span=timedelta(hours=1),
    )
    assert len(prior) == 1
    assert prior[0].start_utc == datetime(2026, 9, 8, 13, 30, tzinfo=UTC)
    assert current is not None
    assert current.start_utc == datetime(2026, 9, 8, 14, 30, tzinfo=UTC)
    assert current.high == 10.4


def test_counterfactual_open_risk_never_consumes_active_budget():
    active = SimpleNamespace(
        id=1,
        selected_contract={
            "paper_policy_id": "OPTIONS_PAPER_V1",
            "planned_risk_dollars": 125.0,
            "paper_evidence_lane": "ACTIVE",
            "risk_budget_consumed": True,
        },
    )
    counterfactual = SimpleNamespace(
        id=2,
        selected_contract={
            "paper_policy_id": "OPTIONS_PAPER_V1",
            "planned_risk_dollars": 250.0,
            "paper_evidence_lane": "COUNTERFACTUAL",
            "risk_budget_consumed": False,
        },
    )

    class Storage:
        def open_setups_after(self, after_id):
            if after_id == 0:
                return [active, counterfactual]
            return []

    assert aggregate_open_planned_risk(Storage()) == 125.0


def test_dashboard_summary_excludes_counterfactual_results(tmp_path):
    storage = ScanStorage(tmp_path / "scanner.sqlite")
    result = ScoreResult("SPY", "LONG", 8, "strat_212", {}, {"option_mark": 2.0})
    scan_id = storage.record_scan(
        result,
        source="test",
        alert_sent=False,
        alert_suppression_reason="",
        timestamp=datetime(2026, 9, 8, 14, 0, tzinfo=UTC),
    )
    active_id = storage.record_shadow_setup(
        result,
        scan_id=scan_id,
        setup_inputs={"option_mark": 2.0},
        provider_snapshot={},
        selected_contract={"paper_policy_id": "OPTIONS_PAPER_V1"},
    )
    cf_id = storage.record_shadow_setup(
        result,
        scan_id=scan_id,
        setup_inputs={"option_mark": 2.0},
        provider_snapshot={},
        selected_contract={
            "paper_policy_id": "OPTIONS_PAPER_V1",
            "paper_evidence_lane": "COUNTERFACTUAL",
            "risk_budget_consumed": False,
        },
    )
    storage.update_shadow_outcome(active_id, status="WIN", outcome={"exit_mark": 3.0})
    storage.update_shadow_outcome(cf_id, status="LOSS", outcome={"exit_mark": 1.0})
    summary = storage.shadow_summary()
    assert summary.total == 1
    assert summary.wins == 1
    assert summary.losses == 0
    assert summary.total_pnl_dollars == 100.0


def test_same_snapshot_stop_and_target_is_labeled_ambiguous_pessimistically():
    class Base:
        async def _resolve_v1_candidate(self, setup, underlying_price, now, chain_cache):
            return (
                "LOSS",
                {
                    "closed_reason": "premium_stop_hit",
                    "option_bid_at_resolution": 1.4,
                    "option_ask_at_resolution": 1.5,
                },
            )

    Hardened = build_v1_evidence_hardening(Base)
    scanner = Hardened()
    setup = SimpleNamespace(
        direction="LONG",
        selected_contract={"premium_stop": 1.5, "target": 110.0},
    )
    status, outcome = asyncio.run(
        scanner._resolve_v1_candidate(
            setup,
            underlying_price=111.0,
            now=datetime(2026, 9, 8, 15, 0, tzinfo=UTC),
            chain_cache={},
        )
    )
    assert status == "LOSS"
    assert outcome["resolution_ambiguity"] == "AMBIGUOUS"
    assert outcome["pessimistic_status"] == "LOSS"
    assert outcome["pessimistic_resolution_used"] is True
    assert outcome["intra_interval_path_known"] is False


def test_v1_preflight_blocks_scheduled_collection_when_bar_context_is_off(monkeypatch):
    pin_v1_env(monkeypatch)

    class Base:
        def __init__(self):
            self.config = SimpleNamespace(
                bar_context_enabled=False,
                bar_context_configured=False,
                market_data_configured=True,
            )
            self.last_skip_reason = None
            self.called = False

        async def scan_watchlist(self, *, source="scheduled", context=None, now=None):
            self.called = True
            return ["called"]

    Scanner = build_v1_runtime_preflight(Base)
    scanner = Scanner()
    result = asyncio.run(scanner.scan_watchlist(source="scheduled"))
    assert result == []
    assert scanner.last_skip_reason == "v1_requires_bar_context"
    assert scanner.called is False


def test_v1_preflight_rejects_policy_pin_mismatch(monkeypatch):
    pin_v1_env(monkeypatch)
    monkeypatch.setenv("OPTIONS_MANAGER_MAX_AGGREGATE_OPEN_RISK_DOLLARS", "750")

    class Base:
        def __init__(self):
            self.config = SimpleNamespace(
                bar_context_enabled=True,
                bar_context_configured=True,
                market_data_configured=True,
            )
            self.last_skip_reason = None

        async def scan_watchlist(self, *, source="scheduled", context=None, now=None):
            return ["should-not-run"]

    Scanner = build_v1_runtime_preflight(Base)
    scanner = Scanner()
    assert asyncio.run(scanner.scan_watchlist(source="scheduled")) == []
    assert scanner.last_skip_reason == "v1_manager_aggregate_risk_mismatch"


def test_v1_preflight_is_inert_outside_explicit_collection_mode(monkeypatch):
    monkeypatch.delenv("OPTIONS_PAPER_V1_COLLECTION_ENABLED", raising=False)

    class Base:
        def __init__(self):
            self.config = SimpleNamespace(
                bar_context_enabled=False,
                bar_context_configured=False,
                market_data_configured=False,
            )
            self.last_skip_reason = None

        async def scan_watchlist(self, *, source="scheduled", context=None, now=None):
            return ["legacy"]

    Scanner = build_v1_runtime_preflight(Base)
    scanner = Scanner()
    assert asyncio.run(scanner.scan_watchlist(source="scheduled")) == ["legacy"]
