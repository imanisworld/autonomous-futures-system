from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest

from research.asian_precursor_audit import (
    LoadedBars,
    build_feature_rows,
    load_bars,
    load_candidates,
    summarize_feature_rows,
    validate_candidate,
)


def bar(ts, o, h, l, c, volume=100, **extra):
    row = {
        "timestamp": ts.isoformat(),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": volume,
        "instrument": "MNQ",
    }
    row.update(extra)
    return row


def make_bars():
    start = datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc)
    rows = []
    px = 20000.0
    for i in range(110):
        ts = start + timedelta(minutes=5 * i)
        drift = 1.0 if i % 4 != 0 else -0.25
        o = px
        c = px + drift
        h = max(o, c) + 0.5
        l = min(o, c) - 0.5
        mc = "RANGE_DEAD" if i < 70 else "TRENDING"
        td = "UP" if i >= 70 else "SIDEWAYS"
        extra = {
            "market_condition": mc,
            "trend_direction": td,
            "trend_strength": "STRONG" if td == "UP" else "WEAK",
            "ema_9": c - 1,
            "ema_21": c - 2,
            "ema_55": c - 3,
            "vwap": c - 4,
            "previous_day_high": 20080.0,
            "previous_day_low": 19920.0,
        }
        if i == 90:
            extra["bos_direction"] = "bullish"
            extra["market_structure"] = "bullish_bos"
        rows.append(bar(ts, o, h, l, c, 100 + i, **extra))
        px = c
    return rows


def loaded(rows):
    return LoadedBars(
        rows=rows,
        timestamps=[datetime.fromisoformat(r["timestamp"]) for r in rows],
        source_files=[],
    )


def candidate(cid, signal_ts, outcome="WIN", direction="LONG", strategy="s"):
    return {
        "candidate_id": cid,
        "instrument": "MNQ",
        "strategy": strategy,
        "session": "asian",
        "direction": direction,
        "signal_ts": signal_ts.isoformat(),
        "outcome_label": outcome,
        "baseline_stop_ticks": 80,
        "target_r": 1.5,
        "relative_volume": 1.2,
    }


def test_candidate_labels_are_explicit_and_asian_only():
    ts = datetime(2026, 9, 2, 1, 0, tzinfo=timezone.utc)
    row = candidate("x", ts)
    assert validate_candidate(row)["outcome_label"] == "WIN"
    with pytest.raises(ValueError, match="never inferred"):
        validate_candidate(dict(row, outcome_label="BREAKEVEN"))
    with pytest.raises(ValueError, match="session must be asian"):
        validate_candidate(dict(row, session="london"))


def test_load_candidates_rejects_duplicate_ids(tmp_path):
    ts = datetime(2026, 9, 2, 1, 0, tzinfo=timezone.utc)
    path = tmp_path / "c.jsonl"
    path.write_text("\n".join(json.dumps(candidate("x", ts)) for _ in range(2)) + "\n")
    with pytest.raises(ValueError, match="duplicate candidate_id"):
        load_candidates(path)


def test_load_bars_rejects_duplicate_timestamps(tmp_path):
    rows = make_bars()[:2]
    rows.append(dict(rows[0]))
    path = tmp_path / "bars.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    with pytest.raises(ValueError, match="duplicate bar timestamp"):
        load_bars(path, instrument="MNQ")


def test_complete_windows_use_only_closed_pre_signal_bars():
    rows = make_bars()
    signal = datetime(2026, 9, 2, 2, 30, tzinfo=timezone.utc)
    c = candidate("x", signal)
    out = build_feature_rows([c], loaded(rows), bar_timestamp_mode="start")[0]
    assert [out["windows"][str(m)]["bar_count"] for m in (15, 30, 60, 120)] == [3, 6, 12, 24]
    assert all(out["windows"][str(m)]["complete"] for m in (15, 30, 60, 120))
    future = bar(signal, 1, 999999, 0, 500000)
    out2 = build_feature_rows([c], loaded(rows + [future]), bar_timestamp_mode="start")[0]
    assert out["windows"] == out2["windows"]
    assert out["session_context"] == out2["session_context"]


def test_incomplete_window_is_visible_not_padded():
    rows = make_bars()
    signal = datetime(2026, 9, 2, 2, 30, tzinfo=timezone.utc)
    missing_ts = signal - timedelta(minutes=25)
    rows = [r for r in rows if datetime.fromisoformat(r["timestamp"]) != missing_ts]
    out = build_feature_rows([candidate("x", signal)], loaded(rows))[0]
    assert out["windows"]["60"]["complete"] is False
    assert out["windows"]["60"]["bar_count"] == 11
    assert "signed_net_points" not in out["windows"]["60"]


def test_transition_ema_structure_and_sweep_features_are_causal():
    rows = make_bars()
    signal = datetime(2026, 9, 2, 2, 30, tzinfo=timezone.utc)
    out = build_feature_rows([candidate("x", signal)], loaded(rows))[0]
    w120 = out["windows"]["120"]
    assert w120["market_condition_last"] == "TRENDING"
    assert w120["trend_direction_last"] == "UP"
    assert w120["ema_stack_aligned_last"] is True
    assert w120["structure_data_present"] is True
    assert w120["structure_aligned_count"] >= 1
    assert w120["latest_structure_aligned"] is True
    assert out["session_context"]["signed_close_vs_source_vwap_points"] > 0


def test_volume_and_true_range_ratios_are_reported_without_threshold_tuning():
    rows = make_bars()
    signal = datetime(2026, 9, 2, 2, 30, tzinfo=timezone.utc)
    out = build_feature_rows([candidate("x", signal)], loaded(rows))[0]
    assert out["windows"]["15"]["volume_ratio_vs_prior_equal_window"] is not None
    assert out["acceleration"]["true_range_ratio_15_to_60"] is not None
    assert out["acceleration"]["volume_ratio_30_to_120"] is not None


def test_session_context_is_directional_and_anchored_at_1800_et():
    rows = make_bars()
    signal = datetime(2026, 9, 2, 2, 30, tzinfo=timezone.utc)
    long = build_feature_rows([candidate("l", signal, direction="LONG")], loaded(rows))[0]
    short = build_feature_rows([candidate("s", signal, direction="SHORT")], loaded(rows))[0]
    assert long["session_context"]["asian_session_bars_before_signal"] > 0
    assert long["session_context"]["signed_close_vs_session_open_points"] == -short["session_context"]["signed_close_vs_session_open_points"]
    assert long["session_context"]["directional_session_range_position"] != short["session_context"]["directional_session_range_position"]


def test_summary_never_pools_instrument_strategy_session_or_direction():
    rows = make_bars()
    base = datetime(2026, 9, 2, 1, 30, tzinfo=timezone.utc)
    cs = [
        candidate("a", base, "WIN", "LONG", "s1"),
        candidate("b", base + timedelta(minutes=15), "LOSS", "LONG", "s1"),
        candidate("c", base + timedelta(minutes=30), "WIN", "SHORT", "s1"),
        candidate("d", base + timedelta(minutes=45), "LOSS", "SHORT", "s1"),
        candidate("e", base + timedelta(minutes=60), "WIN", "LONG", "s2"),
        candidate("f", base + timedelta(minutes=75), "LOSS", "LONG", "s2"),
    ]
    features = build_feature_rows(cs, loaded(rows))
    summary = summarize_feature_rows(features)
    assert len(summary["groups"]) == 3
    assert all(g["instrument"] == "MNQ" and g["session"] == "asian" for g in summary["groups"])
    assert sorted((g["strategy"], g["direction"]) for g in summary["groups"]) == [
        ("s1", "LONG"), ("s1", "SHORT"), ("s2", "LONG")
    ]


def test_chronological_halves_are_assigned_within_matched_cohort():
    rows = make_bars()
    base = datetime(2026, 9, 2, 1, 0, tzinfo=timezone.utc)
    cs = [candidate(str(i), base + timedelta(minutes=15*i), "WIN" if i % 2 == 0 else "LOSS") for i in range(6)]
    features = build_feature_rows(cs, loaded(rows))
    assert [r["sample_half"] for r in features] == ["H1", "H1", "H1", "H2", "H2", "H2"]


def test_summary_compares_input_labels_not_recomputed_outcomes():
    rows = make_bars()
    base = datetime(2026, 9, 2, 1, 30, tzinfo=timezone.utc)
    cs = [candidate("w", base, "WIN"), candidate("l", base + timedelta(minutes=30), "LOSS")]
    features = build_feature_rows(cs, loaded(rows))
    group = summarize_feature_rows(features)["groups"][0]
    assert group["wins"] == 1 and group["losses"] == 1
    assert group["has_both_outcomes"] is True
    assert "candidate.baseline_stop_ticks" in group["numeric_comparison"]


def test_one_run_refuses_mixed_instruments():
    rows = make_bars()
    ts = datetime(2026, 9, 2, 1, 30, tzinfo=timezone.utc)
    a = candidate("a", ts)
    b = candidate("b", ts + timedelta(minutes=15))
    b["instrument"] = "MES"
    with pytest.raises(ValueError, match="do not pool instruments"):
        build_feature_rows([a, b], loaded(rows))
