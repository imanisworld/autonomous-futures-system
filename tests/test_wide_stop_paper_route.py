"""Safety tests for the three-strategy paper-only router."""
from __future__ import annotations

import inspect
from types import SimpleNamespace

from context import five_min_feed
from context import wide_stop_execution as execution
from context import wide_stop_forward_router as router


def test_execution_selector_rejects_any_non_paper_route(monkeypatch):
    monkeypatch.delenv(execution.ROUTE_ENV, raising=False)
    assert execution.route() == "paper_sim"
    assert execution.VALID_ROUTES == ("paper_sim",)
    for value in ("tradovate_demo", "live", "tradovate", "unknown"):
        monkeypatch.setenv(execution.ROUTE_ENV, value)
        assert execution.route() == "disabled"


def test_five_min_campaign_hook_contains_no_external_broker_route():
    source = inspect.getsource(five_min_feed.record_five_min)
    assert "wide_stop_demo_runtime" not in source
    assert "process_demo_five_min_bar" not in source
    assert "tradovate_demo" not in source
    assert "process_paper_five_min_bar" in source


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
