from __future__ import annotations

import copy
import json
from pathlib import Path

from context.market_context import KeyLevels
from replay import ReplayEngine
from strategy.shadow_setups import (
    ShadowSetupCandidate,
    evaluate_shadow_setups,
    resolve_shadow_candidate,
)
from strategy.strat_classifier import StratContext


def _strategies(state, bars=None):
    return {
        candidate.strategy: candidate
        for candidate in evaluate_shadow_setups(state, bars)
    }


def _candidate(direction, entry, stop, target):
    return ShadowSetupCandidate(
        strategy="strat_122_observed",
        direction=direction,
        entry=entry,
        stop=stop,
        target=target,
        rr_ratio=2.0,
        risk_tier="B",
        size_multiplier=0.5,
        notes="test",
    )


def _failed_breakdown_reclaim_fixture(fresh_market_state):
    state = copy.deepcopy(fresh_market_state)
    state.market_condition = "RANGE_BOUND"
    state.ohlc.open = 99.0
    state.ohlc.high = 103.0
    state.ohlc.low = 99.0
    state.ohlc.close = 102.0
    state.volume.current_bar = 1800
    state.volume.avg_bar = 1100
    bars = [
        {"open": 105.0, "high": 106.0, "low": 100.0, "close": 104.0, "volume": 1000},
        {"open": 104.0, "high": 107.0, "low": 101.0, "close": 103.0, "volume": 950},
        {"open": 103.0, "high": 106.0, "low": 100.5, "close": 105.0, "volume": 900},
        {"open": 105.0, "high": 108.0, "low": 101.0, "close": 102.0, "volume": 980},
        {"open": 102.0, "high": 106.5, "low": 100.25, "close": 104.0, "volume": 1025},
        {"open": 104.0, "high": 107.5, "low": 100.75, "close": 103.0, "volume": 990},
        # Sweep/reclaim bar.
        {"open": 101.0, "high": 104.0, "low": 98.5, "close": 101.5, "volume": 1700},
        # Hold bar is represented by state and may also be present in live history.
        {"open": 99.0, "high": 103.0, "low": 99.0, "close": 102.0, "volume": 1800},
    ]
    return state, bars


def test_resolve_shadow_no_fill_when_entry_never_traded():
    cand = _candidate("LONG", entry=100.0, stop=90.0, target=120.0)
    # all forward bars stay below the entry → never fills
    out = resolve_shadow_candidate(cand, [(99.0, 95.0), (98.0, 94.0)], instrument="MNQ")
    assert out.result == "NO_FILL"
    assert out.entry_filled is False
    assert out.pnl_ticks is None


def test_resolve_shadow_win_after_fill():
    cand = _candidate("LONG", entry=100.0, stop=90.0, target=120.0)
    # bar 1 fills entry (range straddles 100), bar 2 reaches target
    out = resolve_shadow_candidate(cand, [(101.0, 99.0), (121.0, 110.0)], instrument="MNQ")
    assert out.result == "WIN"
    assert out.entry_filled is True
    assert out.exit_reason == "TARGET_HIT"
    assert out.bars_to_fill == 1
    assert out.bars_to_exit == 2
    assert out.pnl_ticks == round((120.0 - 100.0) / 0.25, 2)


def test_resolve_shadow_loss_after_fill():
    cand = _candidate("SHORT", entry=100.0, stop=110.0, target=80.0)
    # bar 1 fills (straddles 100), bar 2 hits stop (high >= 110)
    out = resolve_shadow_candidate(cand, [(101.0, 99.0), (112.0, 105.0)], instrument="MNQ")
    assert out.result == "LOSS"
    assert out.exit_reason == "STOP_HIT"


def test_resolve_shadow_both_hit_is_pessimistic_loss():
    cand = _candidate("LONG", entry=100.0, stop=90.0, target=120.0)
    # bar 1 fills entry; bar 2 straddles BOTH stop and target → worst case = LOSS.
    # Resolution starts on the bar AFTER the fill (mirrors PaperBroker, which
    # resolves from the next bar — the fill bar's range is not re-used, since
    # pre-entry excursion would otherwise be counted as a stop-out).
    bars = [(101.0, 99.0), (125.0, 88.0)]
    out = resolve_shadow_candidate(cand, bars, instrument="MNQ")
    assert out.result == "LOSS"
    # opposite assumption flips it to WIN
    optimistic = resolve_shadow_candidate(
        cand, bars, instrument="MNQ", pessimistic_both_hit=False
    )
    assert optimistic.result == "WIN"


def test_resolve_shadow_open_when_unresolved_by_window_end():
    cand = _candidate("LONG", entry=100.0, stop=90.0, target=120.0)
    out = resolve_shadow_candidate(cand, [(101.0, 99.0), (105.0, 98.0)], instrument="MNQ")
    assert out.result == "OPEN"
    assert out.entry_filled is True
    assert out.bars_to_exit is None


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


def test_failed_breakdown_reclaim_observed_only_in_range_transition(
    fresh_market_state,
):
    state, bars = _failed_breakdown_reclaim_fixture(fresh_market_state)

    candidates = _strategies(state, bars)

    reclaim = candidates["transition_failed_breakdown_reclaim"]
    assert reclaim.direction == "LONG"
    assert reclaim.entry == 102.0
    assert reclaim.stop == 98.0
    assert reclaim.target == 104.0
    assert reclaim.risk_tier == "C"
    assert reclaim.size_multiplier == 0.25
    assert "failed breakdown reclaim" in reclaim.notes


def test_failed_breakdown_reclaim_negative_structure_gates(fresh_market_state):
    cases = {
        "range exists but no sweep": lambda state, bars: bars[-2].update({"low": 100.0}),
        "sweep but no reclaim": lambda state, bars: bars[-2].update({"close": 100.0}),
        "reclaim but no hold": lambda state, bars: (
            bars[-1].update({"low": 98.0}),
            setattr(state.ohlc, "low", 98.0),
        ),
        "hold but no expansion": lambda state, bars: (
            bars[-2].update({"volume": 1000}),
            bars[-1].update({"volume": 1000}),
        ),
        "expansion but entry too detached": lambda state, bars: (
            bars[-1].update({"high": 108.0, "low": 101.0, "close": 107.0}),
            setattr(state.ohlc, "high", 108.0),
            setattr(state.ohlc, "low", 101.0),
            setattr(state.ohlc, "close", 107.0),
        ),
        "dead choppy random bounce": lambda state, bars: (
            setattr(state, "market_condition", "CHOPPY"),
            bars[-2].update({"low": 100.25, "close": 101.0}),
        ),
    }
    for label, mutate in cases.items():
        state, bars = _failed_breakdown_reclaim_fixture(fresh_market_state)
        mutate(state, bars)

        assert "transition_failed_breakdown_reclaim" not in _strategies(state, bars), label


def test_failed_breakdown_reclaim_not_journaled_as_trend_continuation(
    fresh_market_state,
):
    state = copy.deepcopy(fresh_market_state)
    state.market_condition = "TRENDING"
    bars = [
        {"open": 105.0, "high": 106.0, "low": 100.0, "close": 104.0, "volume": 1000},
        {"open": 104.0, "high": 107.0, "low": 101.0, "close": 103.0, "volume": 950},
        {"open": 103.0, "high": 106.0, "low": 100.5, "close": 105.0, "volume": 900},
        {"open": 105.0, "high": 108.0, "low": 101.0, "close": 102.0, "volume": 980},
        {"open": 102.0, "high": 106.5, "low": 100.25, "close": 104.0, "volume": 1025},
        {"open": 104.0, "high": 107.5, "low": 100.75, "close": 103.0, "volume": 990},
        {"open": 101.0, "high": 104.0, "low": 98.5, "close": 101.5, "volume": 1700},
        {"open": 99.0, "high": 103.0, "low": 98.0, "close": 102.0, "volume": 1800},
    ]

    assert "transition_failed_breakdown_reclaim" not in _strategies(state, bars)


def test_wide_strat_122_records_stop_aware_pullback_without_changing_trade(
    fresh_market_state,
):
    state = copy.deepcopy(fresh_market_state)
    state.instrument = "MES"
    state.strat = StratContext(
        strat_sequence="strat_122",
        strat_direction="LONG",
    )
    state.ohlc.high = 7452.75
    state.ohlc.low = 7436.0
    state.ohlc.close = 7450.25

    candidates = _strategies(state)

    pullback = candidates["strat_122_pullback"]
    assert pullback.direction == "LONG"
    assert pullback.entry == 7450.0
    assert pullback.stop == 7435.0
    assert pullback.target == 7480.0
    assert pullback.rr_ratio == 2.0
    assert pullback.risk_tier == "B"
    assert pullback.size_multiplier == 0.5
    assert "require a pullback limit fill" in pullback.notes


def test_normal_width_strat_122_emits_observed_structural_candidate(
    fresh_market_state,
):
    state = copy.deepcopy(fresh_market_state)
    state.instrument = "MES"
    state.strat = StratContext(
        strat_sequence="strat_122",
        strat_direction="LONG",
    )
    state.ohlc.high = 7452.75
    state.ohlc.low = 7440.0

    candidates = _strategies(state)
    assert "strat_122_pullback" not in candidates
    observed = candidates["strat_122_observed"]
    assert observed.entry == 7453.0
    assert observed.stop == 7439.0
    assert observed.target == 7481.0
    assert "observe-only" in observed.notes


def test_strat_4hr_retrigger_observed_emits_on_strong_trend(fresh_market_state):
    """Demoted 4HR proxy is journaled (observe-only) when the proxy gate is met.

    Mirrors signal_engine._try_strat_4hr_retrigger: MNQ, NY 9:30–11:00 ET,
    ORB reclaimed_high, STRONG up, VWAP-above, vol>=0.7. The stop cap (80t MNQ)
    binds because the raw ORB-low stop is wider.
    """
    state = copy.deepcopy(fresh_market_state)  # MNQ, 10:30 ET, reclaimed_high, vwap above, vol 1.10
    state.trend.strength = "STRONG"

    observed = _strategies(state)["strat_4hr_retrigger_observed"]
    assert observed.direction == "LONG"
    assert observed.entry == 19498.25          # orb.high + 1 tick
    assert observed.stop == 19478.25           # capped at entry - 80 ticks (raw ORB-low stop is wider)
    assert observed.target == 19538.25         # entry + 2R (R = 20.0)
    assert observed.rr_ratio == 2.0
    assert "observe-only" in observed.notes


def test_strat_4hr_retrigger_observed_absent_without_strong_trend(fresh_market_state):
    """The proxy requires STRONG trend; a MODERATE bar must not journal it."""
    state = copy.deepcopy(fresh_market_state)  # fixture trend strength is MODERATE
    assert "strat_4hr_retrigger_observed" not in _strategies(state)


def test_pdf_defined_312_is_journal_only_with_prior_bar_bracket(
    fresh_market_state,
):
    state = copy.deepcopy(fresh_market_state)
    state.instrument = "MES"
    state.strat = StratContext(
        strat_sequence="strat_312",
        strat_direction="LONG",
    )
    state.raw = {"previous_bar_high": 100.0, "previous_bar_low": 95.0}

    candidate = _strategies(state)["strat_312_observed"]

    assert candidate.entry == 100.25
    assert candidate.stop == 94.75
    assert candidate.target == 111.25
    assert "evidence-only" in candidate.notes


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
