from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from context.htf_loader import HTFLookup
from replay.candle_loader import ReplayCandleLoader
from replay.replay_engine import ReplayEngine
from scripts.csv_to_replay import previous_week_extremes
from strategy.confluence_scorer import score_setup
from risk.risk_engine import DailyState
from strategy.signal_engine import DecisionEngine, SetupDetail
from webhook.payload import AlertPayload
from webhook.state_builder import build_market_state


def _ts(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp())


def _bar(iso: str, high: float, low: float) -> dict:
    return {"ts": _ts(iso), "high": high, "low": low}


def _ordinary_week_2026_08_31() -> list[dict]:
    # Mon 08/31 -> Fri 09/04: every expected trade date holds a bar. The
    # week's extreme (20325 / 19775) prints on Friday.
    return [
        _bar("2026-08-31T14:00:00+00:00", 20100.0, 19920.0),   # Mon (10:00 ET)
        _bar("2026-08-31T22:00:00+00:00", 20150.0, 19950.0),   # Mon 18:00 ET -> Tue
        _bar("2026-09-02T14:00:00+00:00", 20200.0, 19900.0),   # Wed
        _bar("2026-09-03T14:00:00+00:00", 20250.0, 19850.0),   # Thu
        _bar("2026-09-04T20:45:00+00:00", 20325.0, 19775.0),   # Fri
    ]


def test_previous_week_extremes_respects_c14_labor_day_trade_week() -> None:
    # The Sunday 09/06 18:00 ET reopen belongs to Tuesday 09/08 under the
    # proven C14 identity, but it is still in the new Monday-started trading
    # week and must see the complete 08/31-09/04 week as [1].
    bars = _ordinary_week_2026_08_31() + [
        _bar("2026-09-06T22:00:00+00:00", 20200.0, 20000.0),
    ]

    levels = previous_week_extremes(bars, "MNQ")

    assert levels[:5] == [(None, None)] * 5
    assert levels[5] == (20325.0, 19775.0)


def test_previous_week_extremes_fails_closed_on_missing_prior_week_trade_date() -> None:
    # Same week with Thursday 09/03 absent from the source: an ordinary
    # trade date with no bar. The partial extreme must NOT be exposed; the
    # P3-validated builder marks this NOT_AVAILABLE and replay now agrees.
    bars = [b for b in _ordinary_week_2026_08_31() if not b["ts"] == _ts("2026-09-03T14:00:00+00:00")]
    bars.append(_bar("2026-09-06T22:00:00+00:00", 20200.0, 20000.0))

    levels = previous_week_extremes(bars, "MNQ")

    assert levels[-1] == (None, None)


def test_previous_week_extremes_accepts_holiday_shortened_prior_week() -> None:
    # Labor Day week 09/07-09/11: Monday 09/07 is a proven C14 non-trade date
    # (its sessions belong to Tuesday 09/08), so Tue-Fri is a COMPLETE week.
    # The following week must still see its extreme (20400 / 19700).
    bars = [
        _bar("2026-09-06T22:00:00+00:00", 20200.0, 20000.0),   # Sun 18:00 ET -> Tue 09/08
        _bar("2026-09-08T14:00:00+00:00", 20300.0, 19900.0),   # Tue
        _bar("2026-09-09T14:00:00+00:00", 20400.0, 19800.0),   # Wed
        _bar("2026-09-10T14:00:00+00:00", 20350.0, 19700.0),   # Thu
        _bar("2026-09-11T14:00:00+00:00", 20250.0, 19850.0),   # Fri
        _bar("2026-09-13T22:00:00+00:00", 20100.0, 20000.0),   # Sun 18:00 ET -> Mon 09/14
    ]

    levels = previous_week_extremes(bars, "MNQ")

    assert levels[:5] == [(None, None)] * 5
    assert levels[5] == (20400.0, 19700.0)


def _replay_row(**overrides) -> dict:
    row = {
        "timestamp": "2026-09-17T14:30:00+00:00",
        "instrument": "MNQ",
        "session": "new_york",
        "open": 19980.0,
        "high": 20010.0,
        "low": 19975.0,
        "close": 20005.0,
        "volume": 4800,
        "avg_volume": 4000,
        "vwap": 19995.0,
        "price_vs_vwap": "above",
        "orb_high": 19998.0,
        "orb_low": 19960.0,
        "orb_status": "reclaimed_high",
        "market_condition": "TRENDING",
        "trend_direction": "UP",
        "trend_strength": "MODERATE",
        "previous_day_high": 21000.0,
        "previous_day_low": 19000.0,
        "previous_day_close": 19850.0,
        "price_vs_pdh": "below",
        "price_vs_pdl": "above",
        "timeframe": "15m",
        "previous_bar_high": 20000.0,
        "previous_bar_low": 19970.0,
        "hod": 20020.0,
        "lod": 19900.0,
        "prev_week_high": 20050.0,
        "prev_week_low": 19500.0,
        "ema_9": 20000.0,
        "ema_21": 19990.0,
        "ema_55": 19950.0,
        "ema_200": 19800.0,
    }
    row.update(overrides)
    return row


def _load_one(tmp_path: Path, row: dict):
    import json

    path = tmp_path / "one.jsonl"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    return ReplayCandleLoader().load_jsonl(path)[0]


def test_replay_candle_and_state_preserve_prior_week_levels(tmp_path: Path, config) -> None:
    candle = _load_one(tmp_path, _replay_row())

    assert candle.prev_week_high == 20050.0
    assert candle.prev_week_low == 19500.0

    state = ReplayEngine(
        config=config,
        log_dir=str(tmp_path / "replay"),
        htf_lookup=HTFLookup(),
    )._market_state_from_candle(candle)

    assert state.key_levels is not None
    assert state.key_levels.prev_week_high == 20050.0
    assert state.key_levels.prev_week_low == 19500.0


def test_live_and_replay_key_levels_and_pwh_confluence_match(tmp_path: Path, config) -> None:
    payload = AlertPayload(
        ticker="MNQ1!",
        timestamp="2026-09-17T14:30:00+00:00",
        timeframe="15m",
        open=19980.0,
        high=20010.0,
        low=19975.0,
        close=20005.0,
        volume=4800,
        avg_volume=4000,
        vwap=19995.0,
        orb_high=19998.0,
        orb_low=19960.0,
        orb_status="reclaimed_high",
        market_condition="TRENDING",
        trend_direction="UP",
        trend_strength="MODERATE",
        previous_day_high=21000.0,
        previous_day_low=19000.0,
        previous_day_close=19850.0,
        previous_bar_high=20000.0,
        previous_bar_low=19970.0,
        hod=20020.0,
        lod=19900.0,
        prev_week_high=20050.0,
        prev_week_low=19500.0,
        ema_9=20000.0,
        ema_21=19990.0,
        ema_55=19950.0,
        ema_200=19800.0,
    )
    live = build_market_state(payload)

    replay_row = _replay_row(
        trend_direction=live.trend.direction,
        trend_strength=live.trend.strength,
        market_condition=live.market_condition,
    )
    replay = ReplayEngine(
        config=config,
        log_dir=str(tmp_path / "replay"),
        htf_lookup=HTFLookup(),
    )._market_state_from_candle(_load_one(tmp_path, replay_row))

    assert live.key_levels is not None
    assert replay.key_levels is not None
    assert asdict(replay.key_levels) == asdict(live.key_levels)

    setup = SetupDetail(
        direction="LONG",
        entry=20005.0,
        stop=19980.0,
        target=20050.0,
        rr_ratio=1.8,
        strategy="golden_parity_fixture",
    )
    live_score = score_setup(live, setup)
    replay_score = score_setup(replay, setup)

    assert replay_score == live_score
    assert any("Target near PWH" in factor for factor in live_score.factors)


def test_live_and_replay_decision_output_match_on_golden_ny_bar(tmp_path: Path, config) -> None:
    payload = AlertPayload(
        ticker="MNQ1!",
        timestamp="2026-09-17T14:30:00+00:00",
        timeframe="15m",
        open=19480.0,
        high=19510.0,
        low=19475.0,
        close=19505.25,
        volume=4200,
        avg_volume=3800,
        vwap=19495.0,
        vwap_reclaimed=True,
        orb_high=19498.0,
        orb_low=19462.0,
        orb_status="reclaimed_high",
        market_condition="TRENDING",
        trend_direction="UP",
        trend_strength="MODERATE",
        previous_day_high=19520.0,
        previous_day_low=19440.0,
        previous_day_close=19475.0,
        previous_bar_high=19500.0,
        previous_bar_low=19490.0,
        prev_week_high=19600.0,
        prev_week_low=19200.0,
    )
    live = build_market_state(payload)

    # Replay derives vwap_reclaimed from the previous/current bar relationship,
    # so provide the causal prior bar rather than copying the live boolean.
    previous = _load_one(
        tmp_path,
        _replay_row(
            timestamp="2026-09-17T14:15:00+00:00",
            open=19485.0,
            high=19498.0,
            low=19470.0,
            close=19490.0,
            vwap=19495.0,
            price_vs_vwap="below",
            orb_status="inside",
            prev_week_high=19600.0,
            prev_week_low=19200.0,
        ),
    )
    current = _load_one(
        tmp_path,
        _replay_row(
            open=19480.0,
            high=19510.0,
            low=19475.0,
            close=19505.25,
            volume=4200,
            avg_volume=3800,
            vwap=19495.0,
            price_vs_vwap="above",
            orb_high=19498.0,
            orb_low=19462.0,
            orb_status="reclaimed_high",
            market_condition=live.market_condition,
            trend_direction=live.trend.direction,
            trend_strength=live.trend.strength,
            previous_day_high=19520.0,
            previous_day_low=19440.0,
            previous_day_close=19475.0,
            price_vs_pdh="below",
            price_vs_pdl="above",
            previous_bar_high=19500.0,
            previous_bar_low=19490.0,
            prev_week_high=19600.0,
            prev_week_low=19200.0,
            hod=None,
            lod=None,
            ema_9=None,
            ema_21=None,
            ema_55=None,
            ema_200=None,
        ),
    )
    replay = ReplayEngine(
        config=config,
        log_dir=str(tmp_path / "decision-replay"),
        htf_lookup=HTFLookup(),
    )._market_state_from_candle(current, prev_candle=previous)

    live_decision = DecisionEngine(config=config).evaluate(live, DailyState())
    replay_decision = DecisionEngine(config=config).evaluate(replay, DailyState())

    assert replay.vwap.reclaimed is live.vwap.reclaimed is True

    # DecisionOutput.ts is the evaluator's audit-generation timestamp, not a
    # market/strategy fact; two sequential evaluations differ by microseconds.
    # Compare every stable decision/candidate field instead.
    live_dict = live_decision.to_dict()
    replay_dict = replay_decision.to_dict()
    live_dict.pop("ts", None)
    replay_dict.pop("ts", None)
    assert replay_dict == live_dict
