from __future__ import annotations

from scripts.mes_mnq_mechanical_research import (
    Bar,
    Candidate,
    _simulate,
    _target_for,
    analyze_family_roles,
    analyze_stop_timing,
    analyze_target_ambition,
    classify_gates_10way,
    map_gate_label_to_10way,
)


def _bar(ts, high, low, close=None, raw=None):
    return Bar(ts=ts, high=high, low=low, close=close if close is not None else (high + low) / 2, raw=raw or {})


def _candidate(**kw):
    base = dict(
        source="shadow_candidate",
        instrument="MES",
        day="2026-06-01",
        bar_ts="t0",
        session="new_york",
        gate="WEAK_BAR_CLOSE",
        market_condition="TRENDING",
        trend_strength="MODERATE",
        regime="FULL_LONG",
        strategy="orb_reclaim",
        direction="LONG",
        entry=100.0,
        stop=95.0,
        target=115.0,
    )
    base.update(kw)
    return Candidate(**base)


# ─── _simulate: pessimistic same-bar handling ───────────────────────────────

def test_simulate_pessimistic_stop_wins_on_same_bar_ambiguity(monkeypatch):
    import scripts.mes_mnq_mechanical_research as mod

    # _simulate fills entry on the first forward bar whose range covers it,
    # then resolves win/loss starting from the NEXT bar after the fill bar —
    # so a fill-only bar followed by an ambiguous bar needs 3 bars total
    # (decision bar, fill bar, resolution bar).
    c = _candidate(bar_ts="t0", entry=100.0, stop=95.0, target=115.0, instrument="MES")
    candles = [
        _bar("t0", 90, 85),   # decision bar (not used for fill/resolution itself)
        _bar("t1", 101, 99),  # fill bar: entry 100 trades
        _bar("t2", 116, 94),  # both stop (<=95) and target (>=115) touched in one bar
    ]
    monkeypatch.setitem(mod._CANDLE_CACHE, ("MES", "2026-06-01"), candles)
    result = _simulate(c, c.target)
    assert result["result"] == "LOSS"
    assert result["pnl"] == (95.0 - 100.0) * mod.POINT_VALUE["MES"]


def test_simulate_win_when_only_target_hit(monkeypatch):
    import scripts.mes_mnq_mechanical_research as mod

    c = _candidate(bar_ts="t0", entry=100.0, stop=95.0, target=115.0, instrument="MES")
    candles = [
        _bar("t0", 90, 85),
        _bar("t1", 101, 99),   # fill bar
        _bar("t2", 116, 100),  # target hit, stop never touched
    ]
    monkeypatch.setitem(mod._CANDLE_CACHE, ("MES", "2026-06-01"), candles)
    result = _simulate(c, c.target)
    assert result["result"] == "WIN"


def test_simulate_no_fill_when_entry_never_traded_through(monkeypatch):
    import scripts.mes_mnq_mechanical_research as mod

    c = _candidate(bar_ts="t0", entry=100.0, stop=95.0, target=115.0, instrument="MES")
    candles = [
        _bar("t0", 90, 85),
        _bar("t1", 92, 88),  # entry 100 never trades
    ]
    monkeypatch.setitem(mod._CANDLE_CACHE, ("MES", "2026-06-01"), candles)
    result = _simulate(c, c.target)
    assert result["result"] == "NO_FILL"


def test_simulate_no_data_when_bar_ts_missing(monkeypatch):
    import scripts.mes_mnq_mechanical_research as mod

    c = _candidate(bar_ts="missing_ts", instrument="MES")
    monkeypatch.setitem(mod._CANDLE_CACHE, ("MES", "2026-06-01"), [_bar("t0", 101, 99)])
    result = _simulate(c, c.target)
    assert result["result"] == "NO_DATA"


# ─── _target_for: all modes ──────────────────────────────────────────────────

def test_target_for_current_mode_returns_original_target():
    c = _candidate(entry=100.0, stop=95.0, target=115.0)
    assert _target_for(c, "current", None) == 115.0


def test_target_for_r_multiples():
    c = _candidate(entry=100.0, stop=95.0, target=115.0, direction="LONG")
    risk = 5.0
    assert _target_for(c, "0.5R", None) == 100.0 + 0.5 * risk
    assert _target_for(c, "0.75R", None) == 100.0 + 0.75 * risk
    assert _target_for(c, "1.0R", None) == 100.0 + 1.0 * risk


def test_target_for_r_multiple_short_direction():
    c = _candidate(entry=100.0, stop=105.0, target=85.0, direction="SHORT")
    risk = 5.0
    assert _target_for(c, "1.0R", None) == 100.0 - 1.0 * risk


def test_target_for_next_level_picks_nearest_beyond_entry_long():
    c = _candidate(entry=100.0, stop=95.0, target=115.0, direction="LONG")
    bar = _bar("t0", 101, 99, raw={"orb_high": 110.0, "previous_day_high": 130.0, "vwap": 90.0})
    assert _target_for(c, "next_level", bar) == 110.0  # nearest level beyond entry, not the farther one


def test_target_for_next_level_none_when_no_level_beyond_entry():
    c = _candidate(entry=100.0, stop=95.0, target=115.0, direction="LONG")
    bar = _bar("t0", 101, 99, raw={"vwap": 90.0, "previous_day_low": 80.0})
    assert _target_for(c, "next_level", bar) is None


def test_target_for_none_when_risk_is_zero():
    c = _candidate(entry=100.0, stop=100.0, target=115.0)
    assert _target_for(c, "current", None) is None


# ─── 10-way taxonomy mapping ─────────────────────────────────────────────────

def test_map_gate_label_insufficient_data_passthrough():
    assert map_gate_label_to_10way("INSUFFICIENT_DATA", None, None) == "INSUFFICIENT_DATA"


def test_map_gate_label_too_strict_to_overfiltered():
    assert map_gate_label_to_10way("TOO_STRICT", 5.0, 2.0) == "OVERFILTERED"


def test_map_gate_label_good_block_bad_setup_to_bad_strategy():
    assert map_gate_label_to_10way("GOOD_BLOCK_BAD_SETUP", -5.0, -3.0) == "BAD_STRATEGY"


def test_map_gate_label_valid_protection_to_wait():
    assert map_gate_label_to_10way("VALID_PROTECTION", -5.0, -3.0) == "WAIT"


def test_map_gate_label_mixed_with_better_1r_to_trend_modifier_candidate():
    assert map_gate_label_to_10way("MIXED", -2.0, 3.0) == "TREND_MODIFIER_CANDIDATE"


def test_map_gate_label_mixed_without_better_1r_to_wait():
    assert map_gate_label_to_10way("MIXED", 1.0, 0.5) == "WAIT"
    assert map_gate_label_to_10way("MIXED", 1.0, None) == "WAIT"


def test_map_gate_label_unrecognized_raises():
    import pytest

    with pytest.raises(ValueError):
        map_gate_label_to_10way("SOME_UNKNOWN_LABEL", 1.0, 1.0)


def test_classify_gates_10way_wraps_existing_classification():
    gate_classification = {
        "MES|WEAK_BAR_CLOSE|shadow_candidate": {
            "classification": "TOO_STRICT",
            "current_target": {"expectancy": 5.0},
            "target_1R": {"expectancy": 2.0},
        }
    }
    out = classify_gates_10way(gate_classification)
    assert out["MES|WEAK_BAR_CLOSE|shadow_candidate"]["classification_10way"] == "OVERFILTERED"
    assert out["MES|WEAK_BAR_CLOSE|shadow_candidate"]["classification_5way"] == "TOO_STRICT"


# ─── analyze_target_ambition ──────────────────────────────────────────────────

def test_analyze_target_ambition_flags_too_ambitious_cell():
    target_analysis = {
        "cell_by_instrument_strategy_session_gate_source_target": {
            "MES|orb_reclaim|new_york|WEAK_BAR_CLOSE|shadow_candidate|current": {"cases": 50, "expectancy": -5.0},
            "MES|orb_reclaim|new_york|WEAK_BAR_CLOSE|shadow_candidate|0.5R": {"cases": 50, "expectancy": 1.0},
            "MES|orb_reclaim|new_york|WEAK_BAR_CLOSE|shadow_candidate|0.75R": {"cases": 50, "expectancy": 6.0},
            "MES|orb_reclaim|new_york|WEAK_BAR_CLOSE|shadow_candidate|1.0R": {"cases": 50, "expectancy": 3.0},
        }
    }
    out = analyze_target_ambition(target_analysis)
    key = "MES|orb_reclaim|new_york|WEAK_BAR_CLOSE|shadow_candidate"
    assert out[key]["target_too_ambitious"] is True
    assert out[key]["best_smaller_mode"] == "0.75R"


def test_analyze_target_ambition_not_flagged_when_current_already_positive():
    target_analysis = {
        "cell_by_instrument_strategy_session_gate_source_target": {
            "MES|orb_reclaim|new_york|WEAK_BAR_CLOSE|shadow_candidate|current": {"cases": 50, "expectancy": 5.0},
            "MES|orb_reclaim|new_york|WEAK_BAR_CLOSE|shadow_candidate|1.0R": {"cases": 50, "expectancy": 8.0},
        }
    }
    out = analyze_target_ambition(target_analysis)
    key = "MES|orb_reclaim|new_york|WEAK_BAR_CLOSE|shadow_candidate"
    assert out[key]["target_too_ambitious"] is False


def test_analyze_target_ambition_skips_below_min_cell_n():
    target_analysis = {
        "cell_by_instrument_strategy_session_gate_source_target": {
            "MES|orb_reclaim|new_york|WEAK_BAR_CLOSE|shadow_candidate|current": {"cases": 5, "expectancy": -5.0},
            "MES|orb_reclaim|new_york|WEAK_BAR_CLOSE|shadow_candidate|1.0R": {"cases": 5, "expectancy": 8.0},
        }
    }
    out = analyze_target_ambition(target_analysis)
    assert out == {}


# ─── analyze_stop_timing ──────────────────────────────────────────────────────

def test_analyze_stop_timing_flags_problem_when_widening_helps_and_later_target_share_high():
    stop_analysis = {
        "wider_stop_overall": {"1.0": {"losses": 100}},
        "loss_later_target_count": 30,  # 30% share, >= 0.15 threshold
        "wider_stop_by_instrument": {
            "MES|1.0": {"cases": 100, "net_pnl": 100.0, "expectancy": 1.0},
            "MES|1.25": {"cases": 100, "net_pnl": 150.0, "expectancy": 1.5},
            "MES|1.5": {"cases": 100, "net_pnl": 90.0, "expectancy": 0.9},
            "MES|2.0": {"cases": 100, "net_pnl": 80.0, "expectancy": 0.8},
        },
    }
    out = analyze_stop_timing(stop_analysis)
    assert out["by_instrument"]["MES"]["stop_timing_problem"] is True
    assert out["by_instrument"]["MES"]["best_wider_mult"] == "1.25"


def test_analyze_stop_timing_not_flagged_when_widening_does_not_help():
    stop_analysis = {
        "wider_stop_overall": {"1.0": {"losses": 100}},
        "loss_later_target_count": 30,
        "wider_stop_by_instrument": {
            "MES|1.0": {"cases": 100, "net_pnl": 200.0, "expectancy": 2.0},
            "MES|1.25": {"cases": 100, "net_pnl": 150.0, "expectancy": 1.5},
            "MES|1.5": {"cases": 100, "net_pnl": 90.0, "expectancy": 0.9},
            "MES|2.0": {"cases": 100, "net_pnl": 80.0, "expectancy": 0.8},
        },
    }
    out = analyze_stop_timing(stop_analysis)
    assert out["by_instrument"]["MES"]["stop_timing_problem"] is False


def test_analyze_stop_timing_not_flagged_when_later_target_share_low():
    stop_analysis = {
        "wider_stop_overall": {"1.0": {"losses": 100}},
        "loss_later_target_count": 5,  # 5% share, below 0.15 threshold
        "wider_stop_by_instrument": {
            "MES|1.0": {"cases": 100, "net_pnl": 100.0, "expectancy": 1.0},
            "MES|1.25": {"cases": 100, "net_pnl": 150.0, "expectancy": 1.5},
        },
    }
    out = analyze_stop_timing(stop_analysis)
    assert out["by_instrument"]["MES"]["stop_timing_problem"] is False


# ─── analyze_family_roles (ORB/VWAP) ──────────────────────────────────────────

def test_analyze_family_roles_insufficient_data_when_no_cell_meets_min_n():
    honest_baselines = {
        "ioc_limit_static": {
            "by_instrument_strategy_session": {
                "MES|orb_reclaim|new_york": {"resolved": 5, "expectancy": 10.0, "net_pnl": 50.0},
            }
        }
    }
    out = analyze_family_roles(honest_baselines, {"orb_reclaim"})
    assert out["ioc_limit_static"]["MES|orb_reclaim"]["classification"] == "INSUFFICIENT_DATA"


def test_analyze_family_roles_validated_for_orb_when_consistent_positive():
    honest_baselines = {
        "ioc_limit_static": {
            "by_instrument_strategy_session": {
                "MES|orb_reclaim|new_york": {"resolved": 50, "expectancy": 10.0, "net_pnl": 500.0},
                "MES|orb_reclaim|london": {"resolved": 40, "expectancy": 5.0, "net_pnl": 200.0},
            }
        }
    }
    out = analyze_family_roles(honest_baselines, {"orb_reclaim"})
    assert out["ioc_limit_static"]["MES|orb_reclaim"]["classification"] == "VALIDATED"


def test_analyze_family_roles_bad_strategy_for_orb_when_consistent_negative():
    honest_baselines = {
        "ioc_limit_static": {
            "by_instrument_strategy_session": {
                "MES|orb_breakout|new_york": {"resolved": 50, "expectancy": -10.0, "net_pnl": -500.0},
            }
        }
    }
    out = analyze_family_roles(honest_baselines, {"orb_breakout"})
    assert out["ioc_limit_static"]["MES|orb_breakout"]["classification"] == "BAD_STRATEGY"


def test_analyze_family_roles_vwap_context_only_on_sign_flip():
    honest_baselines = {
        "ioc_limit_static": {
            "by_instrument_strategy_session": {
                "MES|vwap_hold|new_york": {"resolved": 50, "expectancy": 8.0, "net_pnl": 400.0},
                "MES|vwap_hold|asian": {"resolved": 50, "expectancy": -10.0, "net_pnl": -500.0},
            }
        }
    }
    out = analyze_family_roles(honest_baselines, {"vwap_hold", "vwap_reclaim", "vwap_rejection"})
    assert out["ioc_limit_static"]["MES|vwap_hold"]["classification"] == "VWAP_CONTEXT_ONLY"


def test_analyze_family_roles_orb_sign_flip_is_promising_not_context_only():
    # Same sign-flip shape, but for ORB (not VWAP) the label must NOT be
    # VWAP_CONTEXT_ONLY -- ORB has no such label.
    honest_baselines = {
        "ioc_limit_static": {
            "by_instrument_strategy_session": {
                "MES|orb_breakout|new_york": {"resolved": 50, "expectancy": 8.0, "net_pnl": 400.0},
                "MES|orb_breakout|london": {"resolved": 50, "expectancy": -10.0, "net_pnl": -500.0},
            }
        }
    }
    out = analyze_family_roles(honest_baselines, {"orb_breakout"})
    assert out["ioc_limit_static"]["MES|orb_breakout"]["classification"] == "PROMISING_BUT_UNPROVEN"


def test_analyze_family_roles_excludes_cells_below_min_n_from_decision():
    # A small-n session with a different sign must NOT flip the classification
    # away from what the qualifying (n >= MIN_CELL_N) sessions alone support.
    honest_baselines = {
        "ioc_limit_static": {
            "by_instrument_strategy_session": {
                "MES|orb_breakout|new_york": {"resolved": 34, "expectancy": -3.79, "net_pnl": -128.75},
                "MES|orb_breakout|london": {"resolved": 17, "expectancy": 4.26, "net_pnl": 72.50},
            }
        }
    }
    out = analyze_family_roles(honest_baselines, {"orb_breakout"})
    # Only new_york (n=34 >= 30) counts; london (n=17) is excluded -> single
    # sign (negative) -> BAD_STRATEGY, not PROMISING_BUT_UNPROVEN.
    assert out["ioc_limit_static"]["MES|orb_breakout"]["classification"] == "BAD_STRATEGY"


def test_analyze_family_roles_ignores_strategies_outside_the_requested_set():
    honest_baselines = {
        "ioc_limit_static": {
            "by_instrument_strategy_session": {
                "MES|pdh_reclaim|new_york": {"resolved": 50, "expectancy": 10.0, "net_pnl": 500.0},
            }
        }
    }
    out = analyze_family_roles(honest_baselines, {"orb_reclaim"})
    assert out["ioc_limit_static"] == {}
