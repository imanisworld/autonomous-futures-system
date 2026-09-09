"""Safety tests for the three-strategy evidence router."""
from __future__ import annotations

from types import SimpleNamespace

from context import wide_stop_execution as execution
from context import wide_stop_forward_router as router


def test_execution_selector_defaults_paper_and_rejects_live_like_unknowns(monkeypatch):
    monkeypatch.delenv(execution.ROUTE_ENV, raising=False)
    assert execution.route() == "paper_sim"
    monkeypatch.setenv(execution.ROUTE_ENV, "tradovate_demo")
    assert execution.route() == "tradovate_demo"
    for value in ("live", "tradovate", "unknown"):
        monkeypatch.setenv(execution.ROUTE_ENV, value)
        assert execution.route() == "disabled"


def test_router_does_not_mutate_or_artificially_cap_strategy_config(monkeypatch, tmp_path):
    seen = {}

    def fake_wide_stop(**kwargs):
        seen["day_cfg_is_original"] = kwargs["cfg"] is cfg
        seen["max_trades_per_day"] = kwargs["cfg"].max_trades_per_day
        seen["bonus_trades_after_max"] = kwargs["cfg"].bonus_trades_after_max
        return []

    def fake_daily(**kwargs):
        seen["daily_cfg_is_original"] = kwargs["cfg"] is cfg
        return []

    monkeypatch.setattr(router, "process_wide_stop", fake_wide_stop)
    monkeypatch.setattr(router, "process_daily_22", fake_daily)
    cfg = SimpleNamespace(max_trades_per_day=3, bonus_trades_after_max=2)

    router.process_paper_five_min_bar(
        payload=SimpleNamespace(), cfg=cfg, bars_5m=[], log_dir=tmp_path,
    )

    assert seen["day_cfg_is_original"] is True
    assert seen["daily_cfg_is_original"] is True
    assert seen["max_trades_per_day"] == 3
    assert seen["bonus_trades_after_max"] == 2
    assert cfg.max_trades_per_day == 3
    assert cfg.bonus_trades_after_max == 2
