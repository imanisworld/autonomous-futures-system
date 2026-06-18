from __future__ import annotations

import copy
import json
from pathlib import Path

from context.market_context import KeyLevels
from replay import ReplayEngine
from strategy.shadow_setups import evaluate_shadow_setups


def _strategies(state):
    return {candidate.strategy: candidate for candidate in evaluate_shadow_setups(state)}


def test_orb_false_break_fade_detects_failed_low_break(fresh_market_state):
    state = copy.deepcopy(fresh_market_state)
    state.orb.status = "rejected_low"
    state.ohlc.low = state.orb.low - 3.0
    state.ohlc.close = state.orb.low + 4.0

    candidates = _strategies(state)

    assert candidates["orb_false_break_fade"].direction == "LONG"
    assert candidates["orb_false_break_fade"].risk_tier == "B"
    assert candidates["orb_false_break_fade"].size_multiplier == 0.5


def test_overnight_sweep_reclaim_uses_optional_overnight_levels(fresh_market_state):
    state = copy.deepcopy(fresh_market_state)
    state.raw = {"overnight_high": 19508.0, "overnight_low": 19400.0}
    state.ohlc.high = 19512.0
    state.ohlc.close = 19505.0

    candidates = _strategies(state)

    assert candidates["ovn_high_sweep_reclaim"].direction == "SHORT"


def test_gap_fill_uses_rth_open_and_previous_close(fresh_market_state):
    state = copy.deepcopy(fresh_market_state)
    state.raw = {"rth_open": 19520.0}
    state.previous_day.close = 19475.0
    state.ohlc.high = 19522.0
    state.ohlc.close = 19505.0

    candidates = _strategies(state)

    assert candidates["gap_fill"].direction == "SHORT"
    assert candidates["gap_fill"].target == 19475.0
    assert candidates["gap_fill"].risk_tier == "C"
    assert candidates["gap_fill"].size_multiplier == 0.25


def test_ema_pullback_trend_uses_ema_zone(fresh_market_state):
    state = copy.deepcopy(fresh_market_state)
    state.key_levels = KeyLevels(ema_9=19500.0, ema_21=19490.0, ema_55=19480.0)
    state.ohlc.low = 19498.0
    state.ohlc.close = 19505.0

    candidates = _strategies(state)

    assert candidates["ema_pullback_trend"].direction == "LONG"
    assert candidates["ema_pullback_trend"].risk_tier == "B"
    assert candidates["ema_pullback_trend"].size_multiplier == 0.75


def test_replay_journals_shadow_candidates_without_enabling_trade(config, tmp_path):
    config.enabled_concepts = []
    candle = {
        "timestamp": "2026-05-23T14:30:00+00:00",
        "instrument": "MNQ",
        "session": "new_york",
        "open": 19460.0,
        "high": 19472.0,
        "low": 19435.0,
        "close": 19466.0,
        "volume": 4200,
        "avg_volume": 3800,
        "vwap": 19460.0,
        "orb_high": 19498.0,
        "orb_low": 19462.0,
        "orb_status": "rejected_low",
        "market_condition": "TRENDING",
        "trend_direction": "UP",
        "trend_strength": "STRONG",
        "previous_day_high": 19520.0,
        "previous_day_low": 19440.0,
        "previous_day_close": 19475.0,
    }
    replay_path = tmp_path / "shadow.jsonl"
    replay_path.write_text(json.dumps(candle) + "\n")

    report = ReplayEngine(config=config, log_dir=str(tmp_path / "logs")).run(
        replay_path,
        review_date="2026-05-23",
    )

    assert report.approved_trades == 0
    entry = json.loads(Path(report.journal_path).read_text().splitlines()[0])
    assert entry["decision"] == "NO_TRADE"
    assert entry["shadow_candidates"][0]["strategy"] == "orb_false_break_fade"
    assert entry["shadow_candidates"][0]["risk_tier"] == "B"
    assert entry["shadow_candidates"][0]["size_multiplier"] == 0.5
