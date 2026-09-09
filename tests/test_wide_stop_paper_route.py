"""Safety tests for the three-strategy paper-only router."""
from __future__ import annotations

from types import SimpleNamespace

from context import wide_stop_execution as execution
from context import wide_stop_forward_router as router


def test_execution_selector_rejects_any_non_paper_route(monkeypatch):
    monkeypatch.delenv(execution.ROUTE_ENV, raising=False)
    assert execution.route() == "paper_sim"
    for value in ("tradovate_demo", "live", "tradovate", "unknown"):
        monkeypatch.setenv(execution.ROUTE_ENV, value)
        assert execution.route() == "disabled"


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
