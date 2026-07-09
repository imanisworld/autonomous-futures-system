from __future__ import annotations

import json

import pytest

from scripts.missed_move_gate_sweep_622d import (
    GATE_TAXONOMY,
    RowClassification,
    _gate_category,
    _pair_outcomes,
    classify_windows,
    find_move_windows,
)


# ─── Gate taxonomy ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("gate,expected_category", list(GATE_TAXONOMY.items()))
def test_gate_category_mapping_matches_taxonomy(gate, expected_category):
    assert _gate_category(gate) == expected_category


def test_gate_category_raises_on_unknown_gate():
    with pytest.raises(ValueError):
        _gate_category("SOME_NEW_GATE_NOBODY_MAPPED_YET")


# ─── Outcome pairing ─────────────────────────────────────────────────────────

def test_pair_outcomes_matches_trade_to_next_outcome():
    rows = [
        {"decision": "TRADE", "bar_ts": "2026-06-01T14:00:00+00:00"},
        {"type": "OUTCOME", "outcome": {"result": "WIN"}},
    ]
    pairs = _pair_outcomes(rows)
    assert pairs["2026-06-01T14:00:00+00:00"]["outcome"]["result"] == "WIN"


def test_pair_outcomes_no_pending_trade_ignored():
    rows = [{"type": "OUTCOME", "outcome": {"result": "WIN"}}]
    assert _pair_outcomes(rows) == {}


def test_pair_outcomes_multiple_trades_sequential():
    rows = [
        {"decision": "TRADE", "bar_ts": "t1"},
        {"type": "OUTCOME", "outcome": {"result": "LOSS"}},
        {"decision": "TRADE", "bar_ts": "t2"},
        {"type": "OUTCOME", "outcome": {"result": "CANCELLED"}},
    ]
    pairs = _pair_outcomes(rows)
    assert pairs["t1"]["outcome"]["result"] == "LOSS"
    assert pairs["t2"]["outcome"]["result"] == "CANCELLED"


# ─── Move-window detection ──────────────────────────────────────────────────

def _write_candle_day(root, instrument, day, bars):
    """bars: list of (ts, o, h, l, c)."""
    d = root / instrument
    d.mkdir(parents=True, exist_ok=True)
    lines = []
    for ts, o, h, l, c in bars:
        lines.append(json.dumps({
            "timestamp": ts, "instrument": instrument, "session": "ny_am",
            "open": o, "high": h, "low": l, "close": c, "volume": 1000, "vwap": c,
            "orb_high": h, "orb_low": l, "orb_status": None,
            "market_condition": "TRENDING", "trend_direction": "UP", "trend_strength": "STRONG",
            "previous_day_high": h, "previous_day_low": l, "previous_day_close": c,
        }))
    (d / f"{instrument}_{day}.jsonl").write_text("\n".join(lines) + "\n")


def test_find_move_windows_flags_large_range_block(tmp_path, monkeypatch):
    import scripts.missed_move_gate_sweep_622d as mod

    monkeypatch.setattr(mod, "CANDLE_ROOT", tmp_path)
    bars = [
        (f"2026-06-01T{h:02d}:00:00+00:00", 100 + h, 105 + h, 98 + h, 102 + h)
        for h in range(4)
    ]
    # inflate range on the block: high jumps far above, low drops far below
    bars[2] = ("2026-06-01T02:00:00+00:00", 102, 140, 60, 100)
    _write_candle_day(tmp_path, "MES", "2026-06-01", bars)

    windows = mod.find_move_windows("MES", "2026-06-01", window_bars=4, threshold_points={"MES": 15.0, "MNQ": 60.0})
    assert len(windows) == 1
    assert windows[0].range_points >= 15.0


def test_find_move_windows_skips_flat_block(tmp_path, monkeypatch):
    import scripts.missed_move_gate_sweep_622d as mod

    monkeypatch.setattr(mod, "CANDLE_ROOT", tmp_path)
    bars = [
        (f"2026-06-01T{h:02d}:00:00+00:00", 100.0, 100.5, 99.5, 100.0)
        for h in range(4)
    ]
    _write_candle_day(tmp_path, "MES", "2026-06-01", bars)

    windows = mod.find_move_windows("MES", "2026-06-01", window_bars=4, threshold_points={"MES": 15.0, "MNQ": 60.0})
    assert windows == []


def test_find_move_windows_missing_file_returns_empty(tmp_path, monkeypatch):
    import scripts.missed_move_gate_sweep_622d as mod

    monkeypatch.setattr(mod, "CANDLE_ROOT", tmp_path)
    assert mod.find_move_windows("MES", "2026-06-01") == []


# ─── Row classification ──────────────────────────────────────────────────────

def _write_journal_day(root, instrument, day, rows):
    d = root / instrument
    d.mkdir(parents=True, exist_ok=True)
    (d / f"journal_{day}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _window(instrument, day, bar_ts_set):
    from scripts.missed_move_gate_sweep_622d import MoveWindow
    return MoveWindow(instrument=instrument, day=day, start_ts="", end_ts="", range_points=99.0, bar_ts_set=set(bar_ts_set))


def test_classify_windows_detected_and_traded(tmp_path, monkeypatch):
    import scripts.missed_move_gate_sweep_622d as mod

    monkeypatch.setattr(mod, "JOURNAL_ROOT", tmp_path)
    rows = [
        {"decision": "TRADE", "bar_ts": "2026-06-01T14:00:00+00:00", "setup": {"strategy": "orb_breakout"}},
        {"type": "OUTCOME", "outcome": {"result": "WIN"}},
    ]
    _write_journal_day(tmp_path, "MES", "2026-06-01", rows)
    result = classify_windows("MES", "2026-06-01", [_window("MES", "2026-06-01", ["2026-06-01T14:00:00+00:00"])])
    assert len(result) == 1
    assert result[0].classification == "DETECTED_AND_TRADED"
    assert result[0].strategy == "orb_breakout"


def test_classify_windows_detected_but_no_fill(tmp_path, monkeypatch):
    import scripts.missed_move_gate_sweep_622d as mod

    monkeypatch.setattr(mod, "JOURNAL_ROOT", tmp_path)
    rows = [
        {"decision": "TRADE", "bar_ts": "2026-06-01T14:00:00+00:00", "setup": {"strategy": "pdh_reclaim"}},
        {"type": "OUTCOME", "outcome": {"result": "CANCELLED"}},
    ]
    _write_journal_day(tmp_path, "MES", "2026-06-01", rows)
    result = classify_windows("MES", "2026-06-01", [_window("MES", "2026-06-01", ["2026-06-01T14:00:00+00:00"])])
    assert result[0].classification == "DETECTED_BUT_NO_FILL"


def test_classify_windows_detected_but_blocked_non_structure_gate(tmp_path, monkeypatch):
    import scripts.missed_move_gate_sweep_622d as mod

    monkeypatch.setattr(mod, "JOURNAL_ROOT", tmp_path)
    rows = [
        {"decision": "NO_TRADE", "bar_ts": "2026-06-01T14:00:00+00:00", "failed_gates": ["SIGNAL_BAR_VOLUME_TOO_LOW"]},
    ]
    _write_journal_day(tmp_path, "MES", "2026-06-01", rows)
    result = classify_windows("MES", "2026-06-01", [_window("MES", "2026-06-01", ["2026-06-01T14:00:00+00:00"])])
    assert result[0].classification == "DETECTED_BUT_BLOCKED"
    assert result[0].gate_category == "volume"


def test_classify_windows_structure_present_but_not_qualified_when_shadow_exists(tmp_path, monkeypatch):
    import scripts.missed_move_gate_sweep_622d as mod

    monkeypatch.setattr(mod, "JOURNAL_ROOT", tmp_path)
    rows = [
        {
            "decision": "NO_TRADE",
            "bar_ts": "2026-06-01T14:00:00+00:00",
            "failed_gates": ["REGIME_NOT_FULL"],
            "shadow_candidates": [{"strategy": "strat_22_reversal_observed"}],
        },
    ]
    _write_journal_day(tmp_path, "MES", "2026-06-01", rows)
    result = classify_windows("MES", "2026-06-01", [_window("MES", "2026-06-01", ["2026-06-01T14:00:00+00:00"])])
    assert result[0].classification == "STRUCTURE_PRESENT_BUT_NOT_QUALIFIED"


def test_classify_windows_no_covered_structure_present_when_no_shadow(tmp_path, monkeypatch):
    import scripts.missed_move_gate_sweep_622d as mod

    monkeypatch.setattr(mod, "JOURNAL_ROOT", tmp_path)
    rows = [
        {"decision": "NO_TRADE", "bar_ts": "2026-06-01T14:00:00+00:00", "failed_gates": ["WEAK_BAR_CLOSE"], "shadow_candidates": []},
    ]
    _write_journal_day(tmp_path, "MES", "2026-06-01", rows)
    result = classify_windows("MES", "2026-06-01", [_window("MES", "2026-06-01", ["2026-06-01T14:00:00+00:00"])])
    assert result[0].classification == "NO_COVERED_STRUCTURE_PRESENT"


def test_classify_windows_no_gate_logged(tmp_path, monkeypatch):
    import scripts.missed_move_gate_sweep_622d as mod

    monkeypatch.setattr(mod, "JOURNAL_ROOT", tmp_path)
    rows = [{"decision": "NO_TRADE", "bar_ts": "2026-06-01T14:00:00+00:00", "failed_gates": []}]
    _write_journal_day(tmp_path, "MES", "2026-06-01", rows)
    result = classify_windows("MES", "2026-06-01", [_window("MES", "2026-06-01", ["2026-06-01T14:00:00+00:00"])])
    assert result[0].classification == "NO_GATE_LOGGED"


def test_classify_windows_no_row_logged_when_bar_missing_from_journal(tmp_path, monkeypatch):
    import scripts.missed_move_gate_sweep_622d as mod

    monkeypatch.setattr(mod, "JOURNAL_ROOT", tmp_path)
    _write_journal_day(tmp_path, "MES", "2026-06-01", [])
    result = classify_windows("MES", "2026-06-01", [_window("MES", "2026-06-01", ["2026-06-01T14:00:00+00:00"])])
    assert result[0].classification == "NO_ROW_LOGGED"


def test_classify_windows_empty_when_no_windows():
    assert classify_windows("MES", "2026-06-01", []) == []
