"""Phase 2A helpers: the armed wide-stop DEMO lane and 5-minute bar data.

Reuses the demo lane's own test fakes and the canonical 3-2-2 candidate
fixture by import only (neither file is edited). No network, box or Tradovate
contact: the broker is always an in-memory fake.
"""
from __future__ import annotations

import copy
from datetime import datetime
from types import SimpleNamespace

from tests.fault_injection._harness import FaultSetupError
from tests.test_wide_stop_demo_runtime import (  # noqa: F401 — re-exported
    DAY, FOUR_HR, _cfg as demo_cfg, _demo_env as demo_env, _eod_broker as eod_broker,
    _FakeBroker as FakeDemoBroker, _patch_candidate as patch_candidate,
    _payload as demo_payload, _root as demo_root,
    _save_open_demo_position as save_open_demo_position,
)
from tests.test_wide_stop_forward_candidate_feed import (
    DAY as FIXTURE_DAY, _bars as fixture_bars, _market_state as fixture_market_state,
)

FIXTURE_EPOCH = "2026-06-15T00:00:00+00:00"
FIXTURE_STOP_LOW = 90  # the 9AM bar low the canonical 3-2-2 long anchors its stop on


def require(cond: bool, what: str) -> None:
    if not cond:
        raise FaultSetupError(f"fault setup not reached: {what}")


def fixture_cfg(config):
    """Isolated config under which the canonical 3-2-2 fixture yields a real,
    risk-approved demo order (proven by the control case)."""
    cfg = copy.copy(config)
    cfg.enabled_concepts = ["orb_breakout"]
    cfg.strategy_permission_gate_enabled = True
    cfg.strategy_permission_default_status = "SHADOW_ONLY"
    cfg.strategy_status = {"orb_breakout": "SHADOW_ONLY"}
    cfg.disabled_concepts_per_instrument = {"MNQ": []}
    cfg.require_trending_condition = True
    cfg.min_rr_ratio = 2.0
    cfg.min_target_points = {"MNQ": 0}
    cfg.wide_stop_ledger_mode = "paper_sim"
    cfg.wide_stop_ledger_epoch_start = FIXTURE_EPOCH
    cfg.daily_22_epoch_start = FIXTURE_EPOCH
    cfg.schedule_mode = "current"
    cfg.demo_execution_hold_sessions = []
    cfg.paper_eligible_sessions = ["new_york"]
    return cfg


def arm_fixture(monkeypatch, bars: list[dict]) -> SimpleNamespace:
    """Demo env pins + the fixture's market state; returns the 5m payload."""
    demo_env(monkeypatch)
    monkeypatch.setenv("WIDE_STOP_LEDGER_EPOCH_START", FIXTURE_EPOCH)
    monkeypatch.setenv("DAILY_22_EPOCH_START", FIXTURE_EPOCH)
    state = fixture_market_state(bars)
    import webhook.state_builder as state_builder
    monkeypatch.setattr(state_builder, "build_market_state", lambda _p: copy.deepcopy(state))
    last = bars[-1]
    return SimpleNamespace(
        ticker="MNQ1!", timestamp=last["ts"], timeframe="5m",
        open=100.0, high=105.0, low=99.0, close=104.5, volume=1000,
    )


def run_fixture_bar(config, tmp_path, monkeypatch, bars: list[dict], broker) -> list[dict]:
    import context.wide_stop_demo_runtime as demo
    payload = arm_fixture(monkeypatch, bars)
    return demo.process_demo_five_min_bar(
        payload=payload, cfg=fixture_cfg(config), bars_5m=bars, log_dir=tmp_path,
        for_date=FIXTURE_DAY, broker_factory=lambda: broker,
    )


def mnq_5m(ts: datetime, **overrides):
    from webhook.payload import AlertPayload
    data = dict(
        ticker="MNQ1!", timestamp=ts.isoformat(), timeframe="5m",
        open=20_000.0, high=20_010.0, low=19_995.0, close=20_005.0,
    )
    data.update(overrides)
    return AlertPayload(**data)


def pending_record(key: str = "crash-candidate", direction: str = "LONG") -> dict:
    return {
        "candidate_key": key, "strategy": FOUR_HR, "instrument": "MNQ",
        "session": "new_york", "direction": direction, "planned_entry": 20_000.0,
        "entry": 20_000.0, "stop": 19_950.0, "target": 20_070.0,
        "rr_ratio": 1.4, "contracts": 1,
        "entry_time": "2026-09-08T14:05:00+00:00",
        "client_order_id": "ws-crash", "broker_order_ids": {},
        "trading_date": DAY.isoformat(),
    }
