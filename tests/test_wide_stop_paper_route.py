"""Safety tests for the paper router plus explicit Tradovate demo selector."""
from __future__ import annotations

import inspect
from types import SimpleNamespace

from context import five_min_feed
from context import wide_stop_execution as execution
from context import wide_stop_forward_router as router


def test_execution_selector_allows_only_paper_or_explicit_demo(monkeypatch):
    monkeypatch.delenv(execution.ROUTE_ENV, raising=False)
    assert execution.route() == "paper_sim"
    assert execution.VALID_ROUTES == ("paper_sim", "tradovate_demo")
    monkeypatch.setenv(execution.ROUTE_ENV, "tradovate_demo")
    assert execution.route() == "tradovate_demo"
    for value in ("live", "tradovate", "unknown"):
        monkeypatch.setenv(execution.ROUTE_ENV, value)
        assert execution.route() == "disabled"


def test_five_min_campaign_hook_routes_demo_only_through_isolated_runtime():
    source = inspect.getsource(five_min_feed.record_five_min)
    assert "wide_stop_demo_runtime" in source
    assert "process_demo_five_min_bar" in source
    # Gated on the DEMO_ROUTE constant rather than a magic string.
    assert "DEMO_ROUTE" in source
    assert execution.DEMO_ROUTE == "tradovate_demo"
    assert "process_paper_five_min_bar" in source


# ── paper/demo coexistence: paper is unconditional, demo is additive ─────────

class _StubHistory:
    def record(self, *args, **kwargs):
        return {"recorded": True}

    def recent(self, *args, **kwargs):
        return []


def _coexistence_env(monkeypatch, route_value):
    """Wire record_five_min so only the lane dispatch is under test."""
    import config.settings as settings
    import context.wide_stop_demo_runtime as demo_runtime
    import context.wide_stop_forward_router as fwd_router

    monkeypatch.setenv("WIDE_STOP_LEDGER_MODE", "paper_sim")
    if route_value is None:
        monkeypatch.delenv(execution.ROUTE_ENV, raising=False)
    else:
        monkeypatch.setenv(execution.ROUTE_ENV, route_value)
    monkeypatch.setattr(five_min_feed, "_history", lambda log_dir: _StubHistory())
    monkeypatch.setattr(settings, "load_config", lambda *a, **k: SimpleNamespace())

    calls = []
    monkeypatch.setattr(
        fwd_router, "process_paper_five_min_bar",
        lambda **kwargs: calls.append("paper"),
    )
    monkeypatch.setattr(
        demo_runtime, "process_demo_five_min_bar",
        lambda **kwargs: calls.append("demo"),
    )
    return calls


def _bar():
    return SimpleNamespace(
        ticker="MNQ1!", timestamp="2026-09-09T14:05:00+00:00",
        open=1.0, high=2.0, low=0.5, close=1.5, volume=10,
    )


def test_paper_still_runs_for_every_bar_while_demo_is_armed(monkeypatch, tmp_path):
    calls = _coexistence_env(monkeypatch, execution.DEMO_ROUTE)
    five_min_feed.record_five_min(_bar(), str(tmp_path))
    assert calls == ["paper", "demo"]  # both ran, paper FIRST


def test_demo_is_not_invoked_when_route_is_paper(monkeypatch, tmp_path):
    calls = _coexistence_env(monkeypatch, None)  # unset -> paper_sim default
    five_min_feed.record_five_min(_bar(), str(tmp_path))
    assert calls == ["paper"]


def test_paper_still_runs_when_the_route_value_is_invalid(monkeypatch, tmp_path):
    calls = _coexistence_env(monkeypatch, "some-nonsense")
    five_min_feed.record_five_min(_bar(), str(tmp_path))
    assert calls == ["paper"]  # unconditional; only demo is gated


def test_demo_failure_cannot_suppress_paper_for_the_same_bar(monkeypatch, tmp_path):
    import context.wide_stop_demo_runtime as demo_runtime

    calls = _coexistence_env(monkeypatch, execution.DEMO_ROUTE)

    def boom(**kwargs):
        calls.append("demo-raised")
        raise RuntimeError("demo lane exploded")

    monkeypatch.setattr(demo_runtime, "process_demo_five_min_bar", boom)
    record = five_min_feed.record_five_min(_bar(), str(tmp_path))
    assert calls == ["paper", "demo-raised"]
    assert record == {"recorded": True}  # ingestion still returned normally


def test_paper_failure_does_not_stop_the_demo_lane(monkeypatch, tmp_path):
    import context.wide_stop_forward_router as fwd_router

    calls = _coexistence_env(monkeypatch, execution.DEMO_ROUTE)

    def boom(**kwargs):
        calls.append("paper-raised")
        raise RuntimeError("paper lane exploded")

    monkeypatch.setattr(fwd_router, "process_paper_five_min_bar", boom)
    five_min_feed.record_five_min(_bar(), str(tmp_path))
    assert calls == ["paper-raised", "demo"]  # separate error boundaries


def test_daily_22_is_reached_only_through_the_paper_lane(monkeypatch, tmp_path):
    """Daily 2-2 has no demo path: it hangs off process_paper_five_min_bar only."""
    demo_source = inspect.getsource(five_min_feed.record_five_min)
    assert "process_daily_22" not in demo_source
    router_source = inspect.getsource(router.process_paper_five_min_bar)
    assert "process_daily_22" in router_source
    import context.wide_stop_demo_runtime_core as demo_core
    assert "daily_22" not in inspect.getsource(demo_core)


def test_router_caps_day_strategy_copy_without_mutating_real_config(monkeypatch, tmp_path):
    seen = {}

    def fake_wide_stop(**kwargs):
        seen["max_trades_per_day"] = kwargs["cfg"].max_trades_per_day
        seen["bonus_trades_after_max"] = kwargs["cfg"].bonus_trades_after_max
        return []

    def fake_daily(**kwargs):
        seen["daily_cfg_is_original"] = kwargs["cfg"] is cfg
        return []

    monkeypatch.setattr(router, "process_wide_stop", fake_wide_stop)
    monkeypatch.setattr(router, "process_daily_22", fake_daily)
    monkeypatch.setattr(router, "assert_state_integrity", lambda *_args, **_kwargs: None)
    cfg = SimpleNamespace(max_trades_per_day=3, bonus_trades_after_max=2)
    payload = SimpleNamespace()

    router.process_paper_five_min_bar(
        payload=payload,
        cfg=cfg,
        bars_5m=[],
        log_dir=tmp_path,
    )

    assert seen["max_trades_per_day"] == 1
    assert seen["bonus_trades_after_max"] == 0
    assert seen["daily_cfg_is_original"] is True
    assert cfg.max_trades_per_day == 3
    assert cfg.bonus_trades_after_max == 2


def test_daily_integrity_gate_runs_before_daily_collector(monkeypatch, tmp_path):
    calls = []

    monkeypatch.setattr(router, "process_wide_stop", lambda **_kwargs: [])

    def gate(*_args, **_kwargs):
        calls.append("gate")

    def daily(**_kwargs):
        calls.append("daily")
        return []

    monkeypatch.setattr(router, "assert_state_integrity", gate)
    monkeypatch.setattr(router, "process_daily_22", daily)
    cfg = SimpleNamespace(max_trades_per_day=3, bonus_trades_after_max=0)

    router.process_paper_five_min_bar(
        payload=SimpleNamespace(), cfg=cfg, bars_5m=[], log_dir=tmp_path
    )
    assert calls == ["gate", "daily"]
