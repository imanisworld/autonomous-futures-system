"""Load-bearing candidate-feed proof for the wide-stop forward collector."""
from __future__ import annotations

import copy
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from context.market_context import (
    MarketState,
    OHLCData,
    ORBData,
    PreviousDayData,
    PriceData,
    TrendData,
    VolumeData,
    VWAPData,
)
from context.wide_stop_forward_collector import (
    THREE_TWO_TWO,
    _evaluate_canonical_candidate,
)

ET = ZoneInfo("America/New_York")
DAY = date(2026, 6, 15)


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(DAY.year, DAY.month, DAY.day, hour, minute, tzinfo=ET)


def _bar(hour: int, minute: int, o, h, l, c) -> dict:
    return {
        "ts": _dt(hour, minute).isoformat(),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
    }


def _bars() -> list[dict]:
    """Canonical 3-2-2 long fixture used by the strategy's own tests."""
    bars: list[dict] = []
    for i in range(12):
        bars.append(_bar(7, i * 5, 100, 110 if i == 0 else 105, 100, 103))
    for i in range(12):
        bars.append(
            _bar(8, i * 5, 105, 115 if i == 0 else 108, 95 if i == 1 else 100, 103)
        )
    for i in range(12):
        bars.append(_bar(9, i * 5, 103, 104, 90 if i == 5 else 100, 92))
    bars.append(_bar(10, 0, 100, 101, 99, 100))
    bars.append(_bar(10, 5, 100, 105, 99, 104.5))
    return bars


def _market_state(bars: list[dict]) -> MarketState:
    current = bars[-1]
    return MarketState(
        timestamp=datetime.fromisoformat(current["ts"]),
        instrument="MNQ",
        session="new_york",
        price=PriceData(last=current["close"], bid=current["close"], ask=current["close"]),
        ohlc=OHLCData(
            open=current["open"],
            high=current["high"],
            low=current["low"],
            close=current["close"],
            timeframe="5m",
        ),
        vwap=VWAPData(value=94, price_vs_vwap="above"),
        orb=ORBData(high=200, low=50, timeframe_minutes=15, status="above"),
        previous_day=PreviousDayData(high=100, low=90, close=95),
        volume=VolumeData(current_bar=1000, avg_bar=1000, relative=1),
        market_condition="RANGE_BOUND",
        trend=TrendData(direction="UP", strength="STRONG"),
        raw={},
    )


def test_parked_322_reaches_canonical_collector_without_active_reenable(config, monkeypatch):
    bars = _bars()
    state = _market_state(bars)
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
    cfg.wide_stop_ledger_epoch_start = "2026-06-15T00:00:00+00:00"

    original_enabled = list(cfg.enabled_concepts)
    original_status = dict(cfg.strategy_status)

    import webhook.state_builder as state_builder

    monkeypatch.setattr(state_builder, "build_market_state", lambda _payload: copy.deepcopy(state))
    payload = SimpleNamespace(timestamp=bars[-1]["ts"])

    decision, observed_state, candidate = _evaluate_canonical_candidate(
        payload=payload,
        cfg=cfg,
        bars_5m=bars,
        strategy=THREE_TWO_TWO,
    )

    assert candidate is not None
    assert candidate["direction"] == "LONG"
    assert candidate["entry"] == 104.0
    assert candidate["stop"] == 90.0
    assert candidate["target"] == 115.0
    assert decision.decision == "TRADE"
    assert decision.setup is not None
    assert decision.setup.strategy == THREE_TWO_TWO
    assert observed_state.strat_322_first_live_candidate == candidate

    # The real/active book remains parked throughout the observation.
    assert cfg.enabled_concepts == original_enabled == ["orb_breakout"]
    assert cfg.strategy_status == original_status == {"orb_breakout": "SHADOW_ONLY"}
    assert THREE_TWO_TWO not in cfg.enabled_concepts
