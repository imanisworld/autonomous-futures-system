from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from context.htf_loader import HTFLookup
from replay.candle_loader import ReplayCandleLoader
from replay.replay_engine import ReplayEngine
from scripts.csv_to_replay import previous_week_extremes
from strategy.confluence_scorer import score_setup
from strategy.signal_engine import SetupDetail
from webhook.payload import AlertPayload
from webhook.state_builder import build_market_state


def _ts(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp())


def test_previous_week_extremes_respects_c14_labor_day_trade_week() -> None:
    # Prior week: Mon 08/31 -> Fri 09/04. The Sunday 09/06 18:00 ET reopen
    # belongs to Tuesday 09/08 under the proven C14 identity, but it is still
    # in the new Monday-started trading week and must see 08/31-09/04 as [1].
    bars = [
        {"ts": _ts("2026-08-31T22:00:00+00:00"), "high": 20100.0, "low": 19920.0},
        {"ts": _ts("2026-09-04T20:45:00+00:00"), "high": 20325.0, "low": 19775.0},
        {"ts": _ts("2026-09-06T22:00:00+00:00"), "high": 20200.0, "low": 20000.0},
    ]

    levels = previous_week_extremes(bars, "MNQ")

    assert levels[0] == (None, None)
    assert levels[1] == (None, None)
    assert levels[2] == (20325.0, 19775.0)


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
