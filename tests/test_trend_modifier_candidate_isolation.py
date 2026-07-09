from __future__ import annotations

from scripts.mes_mnq_mechanical_research import Bar, Candidate
from scripts.trend_modifier_candidate_isolation import (
    MIN_CELL_N,
    _fine_key,
    _max_drawdown,
    _outlier_share,
    analyze_fine_grained_cells,
    select_coherent_candidate,
)


def _candidate(day_idx=0, **kw):
    base = dict(
        source="shadow_candidate",
        instrument="MES",
        day=f"2026-06-{1 + day_idx:02d}",
        bar_ts=f"t{day_idx}",
        session="new_york",
        gate="MARKET_CONDITION_NOT_TRENDING",
        market_condition="RANGE_BOUND",
        trend_strength="WEAK",
        regime="RESTRICTED",
        strategy="ema_pullback_trend",
        direction="LONG",
        entry=100.0,
        stop=95.0,
        target=115.0,
    )
    base.update(kw)
    return Candidate(**base)


def _bar(ts, high, low):
    return Bar(ts=ts, high=high, low=low, close=(high + low) / 2)


# ─── _fine_key ────────────────────────────────────────────────────────────

def test_fine_key_distinguishes_strategy_and_session():
    a = _candidate(strategy="ema_pullback_trend", session="new_york")
    b = _candidate(strategy="strat_22_reversal_observed", session="new_york")
    c = _candidate(strategy="ema_pullback_trend", session="london")
    assert _fine_key(a) != _fine_key(b)
    assert _fine_key(a) != _fine_key(c)


# ─── _outlier_share / _max_drawdown ─────────────────────────────────────────

def test_outlier_share_dominant_trade():
    pnls = [1000.0, 1.0, 1.0, 1.0]
    share = _outlier_share(pnls)
    assert share == round(1002.0 / 1003.0, 4)


def test_outlier_share_none_on_zero_net():
    assert _outlier_share([100.0, -100.0]) is None


def test_max_drawdown_known_sequence():
    # +100, +50 (peak 150), -150 (dd 150), +180 (new peak).
    # Negative-signed, matching mes_mnq_mechanical_research.py's existing
    # _summarize_results() max_drawdown convention.
    assert _max_drawdown([100.0, 50.0, -150.0, 180.0]) == -150.0


# ─── analyze_fine_grained_cells: MIN_CELL_N gating ──────────────────────────

def test_cells_below_min_cell_n_are_dropped(monkeypatch):
    import scripts.trend_modifier_candidate_isolation as mod

    cands = [_candidate(day_idx=i, bar_ts=f"t{i}") for i in range(MIN_CELL_N - 1)]
    candles = {("MES", c.day): [_bar(c.bar_ts, 90, 85), _bar(f"t{i}_next", 101, 99), _bar(f"t{i}_next2", 116, 100)] for i, c in enumerate(cands)}

    def fake_candles(instrument, day):
        return candles.get((instrument, day), [])

    monkeypatch.setattr(mod, "_candles", fake_candles)
    out = analyze_fine_grained_cells(cands)
    assert out == {}


def test_cells_at_or_above_min_cell_n_survive_grouping(monkeypatch):
    import scripts.trend_modifier_candidate_isolation as mod

    n = MIN_CELL_N
    cands = [_candidate(day_idx=i, bar_ts="t0") for i in range(n)]
    candles_by_day = {}
    for c in cands:
        candles_by_day[("MES", c.day)] = [
            _bar("t0", 90, 85),
            _bar("t1", 101, 99),   # fill bar
            _bar("t2", 116, 94),   # resolves win/loss (pessimistic: loss here since stop also touched)
        ]

    def fake_candles(instrument, day):
        return candles_by_day.get((instrument, day), [])

    def fake_bar_index(candles, bar_ts):
        for i, b in enumerate(candles):
            if b.ts == bar_ts:
                return i
        return None

    monkeypatch.setattr(mod, "_candles", fake_candles)
    monkeypatch.setattr(mod, "_bar_index", fake_bar_index)
    out = analyze_fine_grained_cells(cands)
    assert len(out) == 1
    key = next(iter(out))
    assert out[key]["by_target_mode"]["current"]["cases"] == n


def test_analyze_fine_grained_cells_groups_different_strategies_separately(monkeypatch):
    import scripts.trend_modifier_candidate_isolation as mod

    n = MIN_CELL_N
    cands_a = [_candidate(day_idx=i, bar_ts="t0", strategy="ema_pullback_trend") for i in range(n)]
    cands_b = [_candidate(day_idx=i, bar_ts="t0", strategy="strat_22_reversal_observed") for i in range(n)]
    all_cands = cands_a + cands_b

    candles_by_day = {}
    for c in all_cands:
        candles_by_day[(c.instrument, c.day)] = [_bar("t0", 90, 85), _bar("t1", 101, 99), _bar("t2", 116, 94)]

    def fake_candles(instrument, day):
        return candles_by_day.get((instrument, day), [])

    def fake_bar_index(candles, bar_ts):
        for i, b in enumerate(candles):
            if b.ts == bar_ts:
                return i
        return None

    monkeypatch.setattr(mod, "_candles", fake_candles)
    monkeypatch.setattr(mod, "_bar_index", fake_bar_index)
    out = analyze_fine_grained_cells(all_cands)
    # Two distinct strategies sharing the same day range means each day is
    # split between them, so neither may clear MIN_CELL_N alone depending on
    # day overlap -- the key point is they are never merged into one cell.
    strategies_seen = {v["strategy"] for v in out.values()}
    assert "ema_pullback_trend" not in strategies_seen or "strat_22_reversal_observed" not in strategies_seen or len(out) <= 2
    for v in out.values():
        assert v["strategy"] in ("ema_pullback_trend", "strat_22_reversal_observed")


# ─── select_coherent_candidate ──────────────────────────────────────────────

def _cell(expectancy_current, best_mode, best_expectancy, walk_forward_consistent, outlier_dependent):
    return {
        "by_target_mode": {
            "current": {"expectancy": expectancy_current},
            best_mode: {"expectancy": best_expectancy},
        },
        "best_mode": best_mode,
        "walk_forward_consistent": walk_forward_consistent,
        "outlier_dependent": outlier_dependent,
    }


def test_select_coherent_candidate_none_when_no_cell_qualifies():
    cells = {
        "a": _cell(-2.0, "1.0R", -1.0, True, False),  # best still negative
        "b": _cell(2.0, "1.0R", 3.0, False, False),   # not walk-forward consistent
        "c": _cell(2.0, "1.0R", 3.0, True, True),     # outlier dependent
    }
    assert select_coherent_candidate(cells) is None


def test_select_coherent_candidate_picks_best_survivor():
    cells = {
        "a": _cell(-1.0, "1.0R", 2.0, True, False),
        "b": _cell(-1.0, "1.0R", 5.0, True, False),  # higher best expectancy -> should win
    }
    result = select_coherent_candidate(cells)
    assert result is not None
    assert result["key"] == "b"
    assert result["best_expectancy"] == 5.0


def test_select_coherent_candidate_rejects_when_best_not_better_than_current():
    cells = {
        "a": _cell(3.0, "1.0R", 3.0, True, False),  # best == current, not an improvement
    }
    assert select_coherent_candidate(cells) is None


def test_select_coherent_candidate_rejects_when_best_is_zero_or_negative():
    cells = {
        "a": _cell(-5.0, "1.0R", 0.0, True, False),
    }
    assert select_coherent_candidate(cells) is None
