from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

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
from journal.journal_logger import JournalLogger
from replay.candle_loader import ReplayCandle
from replay.replay_engine import ReplayEngine
from risk.risk_engine import DailyState
from strategy.four_hr_retrigger import (
    advance_4hr_retrigger,
    aggregate_et_bars,
)
from strategy.signal_engine import DecisionEngine, DecisionOutput, SetupDetail
from strategy.stop_sizing import apply_stop_multiplier
from webhook.payload import AlertPayload
from webhook.runner import process_alert

ET = ZoneInfo("America/New_York")
TUESDAY = date(2026, 1, 6)
MONDAY = date(2026, 1, 5)


def _dt(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET)


def _bar(day: date, hour: int, minute: int, o, h, l, c) -> dict:
    return {
        "ts": _dt(day, hour, minute).astimezone(timezone.utc).isoformat(),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": 1000,
        "timeframe": "5m",
    }


def _long_history(
    day: date = TUESDAY,
    reference_day: date = MONDAY,
    *,
    same_bar: bool = False,
    open_930: float = 94,
    trigger_at: tuple[int, int] | None = (9, 30),
) -> list[dict]:
    bars = [
        _bar(reference_day, 16, 0, 95, 100, 90, 95),
        _bar(day, 4, 0, 90, 95, 85, 88),
        _bar(day, 8, 0, 90, 94, 87, 93),
    ]
    if same_bar:
        bars.append(_bar(day, 8, 5, 93, 96, 92, 94))
    else:
        bars.extend(
            [
                _bar(day, 8, 5, 93, 96, 92, 96),
                _bar(day, 8, 10, 96, 97, 93, 94),
            ]
        )
    bars.append(_bar(day, 9, 0, 94, 94.5, 93, 94))
    if trigger_at != (9, 30):
        bars.append(_bar(day, 9, 30, open_930, 94.5, 93.5, 94))
    if trigger_at is not None:
        hour, minute = trigger_at
        while (9, 35) <= (hour, minute) and (
            bars[-1]["ts"] < _dt(day, hour, minute).astimezone(timezone.utc).isoformat()
        ):
            last = datetime.fromisoformat(bars[-1]["ts"]).astimezone(ET)
            next_open = max(last + timedelta(minutes=5), _dt(day, 9, 35))
            if next_open >= _dt(day, hour, minute):
                break
            bars.append(
                _bar(day, next_open.hour, next_open.minute, 94, 94.5, 93.5, 94)
            )
        bars.append(_bar(day, hour, minute, open_930, 96, 93, 95.5))
    return sorted(bars, key=lambda bar: bar["ts"])


def _short_history() -> list[dict]:
    return [
        _bar(MONDAY, 16, 0, 95, 100, 90, 95),
        _bar(TUESDAY, 4, 0, 98, 105, 92, 100),
        _bar(TUESDAY, 8, 0, 100, 106, 93, 100),
        _bar(TUESDAY, 8, 5, 100, 104, 91, 93),
        _bar(TUESDAY, 9, 0, 93, 104, 92.5, 94),
        _bar(TUESDAY, 9, 30, 93, 94, 91, 91.5),
    ]


def _advance(bars: list[dict], current=(9, 30), state=None, instrument="MNQ"):
    return advance_4hr_retrigger(
        bars_5m=bars,
        current_bar_ts=_dt(TUESDAY, *current),
        instrument=instrument,
        persisted_state=state,
    )


def test_long_resolved_setup_and_prior_4pm_target():
    state, candidate = _advance(_long_history())
    assert state["status"] == "TRIGGERED"
    assert candidate["direction"] == "LONG"
    assert (candidate["entry"], candidate["stop"], candidate["target"]) == (
        95,
        87,
        100,
    )


def test_short_resolved_setup():
    state, candidate = _advance(_short_history())
    assert state["status"] == "TRIGGERED"
    assert (candidate["direction"], candidate["entry"], candidate["target"]) == (
        "SHORT",
        92,
        90,
    )
    assert candidate["stop"] == 106


def test_prebreak_close_through_does_not_confirm_retrigger():
    bars = [
        _bar(MONDAY, 16, 0, 95, 100, 90, 95),
        _bar(TUESDAY, 4, 0, 90, 95, 85, 88),
        _bar(TUESDAY, 8, 0, 93, 94, 90, 94),  # close through, no break
        _bar(TUESDAY, 8, 5, 94, 96, 94, 96),  # break, no close back
        _bar(TUESDAY, 9, 30, 94, 96, 93, 95.5),
    ]
    state, candidate = _advance(bars)
    assert candidate is None
    assert state["invalidation"] == "BREAK_RETRIGGER_NOT_CONFIRMED"


def test_same_bar_break_and_close_back_through_is_valid():
    state, candidate = _advance(_long_history(same_bar=True))
    assert state["status"] == "TRIGGERED"
    assert candidate is not None


def test_no_setup_can_be_established_after_0930():
    state, candidate = _advance(
        _long_history(trigger_at=(9, 35)), current=(9, 35)
    )
    assert candidate is None
    assert state["invalidation"] == "SETUP_NOT_ESTABLISHED_BY_0930"


def test_post_0930_and_noon_bars_cannot_create_developing_setup():
    bars = [
        _bar(MONDAY, 16, 0, 95, 100, 90, 95),
        _bar(TUESDAY, 4, 0, 90, 95, 85, 88),
        _bar(TUESDAY, 8, 0, 90, 94, 87, 93),  # no pre-open break
        _bar(TUESDAY, 9, 30, 94, 94.5, 93, 94),
        _bar(TUESDAY, 9, 35, 94, 110, 80, 94),  # tempting future pattern
        _bar(TUESDAY, 12, 0, 94, 120, 70, 94),  # completed/noon lookahead
    ]
    state, candidate = _advance(bars)
    assert candidate is None
    assert state["invalidation"] == "BREAK_RETRIGGER_NOT_CONFIRMED"


def test_price_through_trigger_at_open_invalidates():
    state, candidate = _advance(_long_history(open_930=96))
    assert candidate is None
    assert state["invalidation"] == "PRICE_THROUGH_TRIGGER_AT_OPEN"


@pytest.mark.parametrize("instrument", ["MNQ", "MES"])
def test_monday_futures_uses_sunday_4pm_reference(instrument):
    monday = date(2026, 1, 12)
    sunday = monday - timedelta(days=1)
    bars = _long_history(monday, sunday)
    state, _ = advance_4hr_retrigger(
        bars_5m=bars,
        current_bar_ts=_dt(monday, 9, 30),
        instrument=instrument,
    )
    assert state["reference_bar_ts"] == _dt(sunday, 16).isoformat()


def test_monday_qqq_uses_friday_4pm_reference():
    monday = date(2026, 1, 12)
    friday = monday - timedelta(days=3)
    bars = _long_history(monday, friday)
    bars.insert(0, _bar(monday - timedelta(days=1), 16, 0, 500, 999, 500, 700))
    state, _ = advance_4hr_retrigger(
        bars_5m=bars,
        current_bar_ts=_dt(monday, 9, 30),
        instrument="QQQ",
    )
    assert state["reference_bar_ts"] == _dt(friday, 16).isoformat()


@pytest.mark.parametrize(
    ("trigger_at", "expected_stop", "expected_stop_bar"),
    [
        ((9, 30), 87, (8, 0)),   # actual entry 09:35
        ((9, 50), 87, (8, 0)),   # actual entry 09:55
        ((10, 0), 93, (9, 0)),   # actual entry 10:05
        ((10, 30), 93, (9, 0)),  # actual entry 10:35
    ],
)
def test_stop_uses_last_completed_one_hour_at_actual_entry(
    trigger_at, expected_stop, expected_stop_bar
):
    bars = _long_history(trigger_at=trigger_at)
    armed, candidate = _advance(bars, state=None)
    if trigger_at != (9, 30):
        assert candidate is None
        armed, candidate = _advance(bars, current=trigger_at, state=armed)
    assert candidate["stop"] == expected_stop
    assert candidate["stop_bar_ts"] == _dt(TUESDAY, *expected_stop_bar)


def test_stop_is_fixed_after_entry():
    triggered, candidate = _advance(_long_history())
    later, next_candidate = _advance(
        _long_history(trigger_at=(10, 30)), current=(10, 30), state=triggered
    )
    assert next_candidate is None
    assert later["stop"] == candidate["stop"] == 87
    assert later["stop_bar_ts"] == candidate["stop_bar_ts"].isoformat()


def test_generic_stop_multiplier_cannot_rewrite_fixed_4hr_stop():
    setup = SetupDetail(
        direction="LONG",
        entry=95,
        stop=87,
        target=100,
        rr_ratio=0.625,
        strategy="strat_4hr_retrigger",
    )
    applied = apply_stop_multiplier(setup, "MNQ", {"MNQ": 2.0})
    assert applied == 1.0
    assert setup.stop == 87


def test_armed_setup_persists_then_expires_at_1100():
    bars = _long_history(trigger_at=None)
    armed, candidate = _advance(bars)
    assert candidate is None
    assert armed["status"] == "ARMED"
    persisted, candidate = _advance(bars, current=(9, 35), state=armed)
    assert persisted == armed
    assert candidate is None
    expired, candidate = _advance(bars, current=(11, 0), state=persisted)
    assert candidate is None
    assert expired["status"] == "EXPIRED"


def test_journal_restart_reconstructs_pending_state(tmp_path):
    armed, _ = _advance(_long_history(trigger_at=None))
    journal = JournalLogger(log_dir=str(tmp_path))
    journal.log_decision(
        {
            "ts": _dt(TUESDAY, 9, 35).isoformat(),
            "instrument": "MNQ",
            "session": "new_york",
            "decision": "NO_TRADE",
            "reason": "pending",
            "strategy_state": {"strat_4hr_retrigger": {"MNQ": armed}},
        },
        None,
        for_date=TUESDAY,
    )
    restored = JournalLogger(log_dir=str(tmp_path)).get_daily_state(TUESDAY)
    assert restored.four_hr_retrigger_state["MNQ"] == armed


@pytest.mark.parametrize(
    ("timestamp", "expected_4h", "expected_1h"),
    [
        (datetime(2026, 1, 6, 21, 5, tzinfo=timezone.utc), (16, 0), (16, 0)),
        (datetime(2026, 7, 6, 20, 5, tzinfo=timezone.utc), (16, 0), (16, 0)),
    ],
)
def test_et_wall_clock_aggregation_is_dst_safe(
    timestamp, expected_4h, expected_1h
):
    bar = {
        "ts": timestamp.isoformat(),
        "open": 1,
        "high": 2,
        "low": 0,
        "close": 1,
    }
    four_hour = aggregate_et_bars([bar], 240)[0]["ts"]
    one_hour = aggregate_et_bars([bar], 60)[0]["ts"]
    assert (four_hour.hour, four_hour.minute) == expected_4h
    assert (one_hour.hour, one_hour.minute) == expected_1h


def _market_state(bars: list[dict]) -> MarketState:
    current = bars[-1]
    ts = datetime.fromisoformat(current["ts"])
    return MarketState(
        timestamp=ts,
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
        orb=ORBData(high=200, low=50, timeframe_minutes=15, status="inside"),
        previous_day=PreviousDayData(high=100, low=90, close=95),
        volume=VolumeData(current_bar=1000, avg_bar=1000, relative=1),
        market_condition="TRENDING",
        trend=TrendData(direction="UP", strength="STRONG"),
        bar_history_5m=deepcopy(bars),
        raw={},
    )


def test_decision_engine_uses_canonical_formula_in_runtime_and_replay(
    config, tmp_path
):
    cfg = replace(
        config,
        enabled_concepts=["strat_4hr_retrigger"],
        strategy_permission_gate_enabled=False,
        require_trending_condition=False,
        require_strong_trend={"MNQ": False},
        min_signal_bar_volume={"MNQ": 0},
        min_rr_ratio=0.0,
        min_target_points={"MNQ": 0},
    )
    runtime_state = _market_state(_long_history())
    current = _long_history()[-1]
    replay_candle = ReplayCandle(
        timestamp=current["ts"],
        instrument="MNQ",
        session="new_york",
        open=current["open"],
        high=current["high"],
        low=current["low"],
        close=current["close"],
        volume=1000,
        vwap=94,
        orb_high=200,
        orb_low=50,
        orb_status="inside",
        market_condition="TRENDING",
        trend_direction="UP",
        trend_strength="STRONG",
        previous_day_high=100,
        previous_day_low=90,
        previous_day_close=95,
        timeframe="5m",
        avg_volume=1000,
    )
    replay_state = ReplayEngine(
        cfg, log_dir=str(tmp_path / "replay")
    )._market_state_from_candle(replay_candle)
    replay_state.bar_history_5m = _long_history()
    runtime = DecisionEngine(cfg).evaluate(runtime_state, DailyState())
    replay = DecisionEngine(cfg).evaluate(replay_state, DailyState())
    assert runtime.decision == replay.decision == "TRADE"
    assert runtime.setup.strategy == replay.setup.strategy == "strat_4hr_retrigger"
    assert runtime.setup.entry == replay.setup.entry == 95
    assert runtime.setup.stop == replay.setup.stop == 87
    assert runtime.setup.target == replay.setup.target == 100


def test_cross_instrument_armed_state_is_not_consumed_by_another_instrument(config):
    """Regression for the shared-DailyState cross-instrument leak: MNQ arms
    the canonical 4HR re-trigger, then an interleaved MES 5-minute bar
    (same DailyState, same wall-clock, unrelated price levels) must not
    trigger against or overwrite MNQ's persisted state. Before instrument-
    keying, four_hr_retrigger_state was a single unkeyed dict — any other
    instrument's next 5m bar would advance/resolve/overwrite it."""
    cfg = replace(
        config,
        enabled_concepts=[*config.enabled_concepts, "strat_4hr_retrigger"],
    )
    engine = DecisionEngine(cfg)
    daily = DailyState()

    mnq_state = _market_state(_long_history(trigger_at=None))
    engine._advance_4hr_retrigger(mnq_state, daily)
    assert daily.four_hr_retrigger_state["MNQ"]["status"] == "ARMED"
    mnq_armed_snapshot = deepcopy(daily.four_hr_retrigger_state["MNQ"])

    # An MES 5m bar arrives next on the same DailyState — same bar history
    # shape, different instrument, unrelated to MNQ's armed boundary.
    mes_state = replace(mnq_state, instrument="MES")
    engine._advance_4hr_retrigger(mes_state, daily)

    # MNQ's armed state must be completely untouched by the MES bar.
    assert daily.four_hr_retrigger_state["MNQ"] == mnq_armed_snapshot
    # MES gets its own independent slot, not MNQ's armed state.
    assert "MES" in daily.four_hr_retrigger_state


def test_instrument_disabled_concept_never_advances_persisted_state(config):
    """disabled_concepts_per_instrument overrides enabled_concepts for one
    instrument. strat_4hr_retrigger is globally enabled but disabled for
    MNQ specifically — the persisted state machine must never advance (or
    arm) for MNQ at all, not just be prevented from producing a trade."""
    cfg = replace(
        config,
        enabled_concepts=[*config.enabled_concepts, "strat_4hr_retrigger"],
        disabled_concepts_per_instrument={"MNQ": ["strat_4hr_retrigger"]},
    )
    engine = DecisionEngine(cfg)
    daily = DailyState()

    mnq_state = _market_state(_long_history(trigger_at=None))
    engine._advance_4hr_retrigger(mnq_state, daily)

    assert "MNQ" not in daily.four_hr_retrigger_state
    assert mnq_state.four_hr_retrigger_candidate is None


def test_instrument_disabled_elsewhere_does_not_block_other_instrument(config):
    """The per-instrument disable for MNQ must not affect MES — MES keeps
    advancing/arming normally."""
    cfg = replace(
        config,
        enabled_concepts=[*config.enabled_concepts, "strat_4hr_retrigger"],
        disabled_concepts_per_instrument={"MNQ": ["strat_4hr_retrigger"]},
    )
    engine = DecisionEngine(cfg)
    daily = DailyState()

    mes_state = replace(_market_state(_long_history(trigger_at=None)), instrument="MES")
    engine._advance_4hr_retrigger(mes_state, daily)

    assert daily.four_hr_retrigger_state["MES"]["status"] == "ARMED"


def test_old_orb_reclaim_proxy_cannot_emit_under_4hr_identity(config):
    cfg = replace(
        config,
        enabled_concepts=["strat_4hr_retrigger"],
        require_trending_condition=False,
    )
    state = _market_state(_long_history())
    state.bar_history_5m = []
    state.orb.status = "reclaimed_high"
    state.vwap.price_vs_vwap = "above"
    decision = DecisionEngine(cfg).evaluate(state, DailyState())
    assert decision.decision == "NO_TRADE"
    assert decision.setup is None


def test_live_five_min_lane_routes_only_canonical_4hr_through_decision_engine(
    config, monkeypatch, tmp_path
):
    monkeypatch.setenv("FIVE_MIN_FEED_ENABLED", "true")
    cfg = replace(
        config,
        enabled_concepts=["strat_4hr_retrigger"],
        strategy_permission_gate_enabled=False,
    )
    ts = datetime(2026, 1, 6, 9, 30, tzinfo=ET)
    payload = AlertPayload(
        ticker="MNQ1!",
        timestamp=ts.astimezone(timezone.utc).isoformat(),
        open=19094,
        high=19096,
        low=19093,
        close=19095.5,
        volume=1000,
        avg_volume=1000,
        timeframe="5m",
        market_condition="TRENDING",
        trend_direction="UP",
        trend_strength="STRONG",
    )
    seen = {}

    def _evaluate(_engine, state, daily_state):
        seen["canonical_only"] = state.canonical_4hr_only
        seen["history"] = list(state.bar_history_5m)
        return DecisionOutput(
            timestamp=state.timestamp,
            instrument=state.instrument,
            session=state.session,
            decision="NO_TRADE",
            reason="focused routing proof",
        )

    monkeypatch.setattr(DecisionEngine, "evaluate", _evaluate)
    result = process_alert(
        payload,
        config=cfg,
        log_dir=str(tmp_path),
        for_date=ts.date(),
    )
    assert result["decision"] == "NO_TRADE"
    assert seen["canonical_only"] is True
    assert len(seen["history"]) == 1
