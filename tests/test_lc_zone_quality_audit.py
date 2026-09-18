from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "lc_zone_quality_audit",
    Path(__file__).parents[1] / "scripts" / "lc_zone_quality_audit_2026_09_18.py",
)
m = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(m)

UTC = timezone.utc


def bar(ts, o=100.0, h=101.0, l=99.0, c=100.5):
    return {"ts": ts, "open": o, "high": h, "low": l, "close": c}


def test_strict_aggregate_requires_full_clock_bucket():
    t0 = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)
    bars = [bar(t0 + timedelta(minutes=15 * i), c=100 + i) for i in range(3)]
    assert m.strict_aggregate(bars, 60) == []

    bars.append(bar(t0 + timedelta(minutes=45), h=105, l=98, c=104))
    out = m.strict_aggregate(bars, 60)
    assert len(out) == 1
    assert out[0]["ts"] == t0
    assert out[0]["_count"] == 4
    assert out[0]["close"] == 104


def test_strict_aggregate_allows_market_gap_once_clock_bucket_completed():
    t0 = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)
    bars = [
        bar(t0),
        bar(t0 + timedelta(minutes=15)),
        # 12:30 absent (market/data gap); 12:00 clock bucket still completes at 13:00.
        bar(t0 + timedelta(minutes=45), c=102.0),
        bar(t0 + timedelta(minutes=60)),
    ]
    out = m.strict_aggregate(bars, 60)
    assert len(out) == 1
    assert out[0]["ts"] == t0
    assert out[0]["_count"] == 3
    assert out[0]["close"] == 102.0


def test_reaction_same_bar_break_beats_rejection_threshold():
    t0 = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)
    # Build enough quiet history for non-zero MTR.
    bars = [
        bar(t0 + timedelta(minutes=15 * i), o=100, h=101, l=99, c=100)
        for i in range(70)
    ]
    idx = 62
    # Demand zone near edge=100, far edge=98.  This touch bar both reaches
    # favorable >0.5 MTR and closes below far edge.  Prereg says failure first.
    bars[idx] = bar(
        bars[idx]["ts"],
        o=100,
        h=104,
        l=97,
        c=97.5,
    )
    e = {"kind": "demand", "top": 100.0, "bottom": 98.0}
    r = m.reaction_from_touch(bars, idx, e)
    assert r["reaction_complete"] is True
    assert r["close_through_2h"] is True
    assert r["clean_rejection_05"] is False
    assert r["clean_rejection_10"] is False


def test_reaction_counts_clean_rejection_when_no_far_edge_close():
    t0 = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)
    bars = [
        bar(t0 + timedelta(minutes=15 * i), o=100, h=101, l=99, c=100)
        for i in range(70)
    ]
    idx = 62
    bars[idx] = bar(bars[idx]["ts"], o=100, h=103, l=99, c=101)
    e = {"kind": "demand", "top": 100.0, "bottom": 98.0}
    r = m.reaction_from_touch(bars, idx, e)
    assert r["reaction_complete"] is True
    assert r["close_through_2h"] is False
    assert r["clean_rejection_05"] is True


def test_quality_classification_requires_cross_instrument_and_timeframe_support():
    summary = {"n": 100, "uplift_pp": 7.0, "uplift_95ci_pp": [2.0, 12.0]}
    inst = {
        "MNQ": {"n": 50, "uplift_pp": 8.0},
        "MES": {"n": 50, "uplift_pp": 6.0},
    }
    tf = {
        "60": {"n": 50, "uplift_pp": 4.0},
        "240": {"n": 50, "uplift_pp": 10.0},
    }
    assert m.classify_quality(summary, inst, tf) == "QUALITY SIGNAL SUPPORTED"

    inst["MES"]["uplift_pp"] = -1.0
    assert m.classify_quality(summary, inst, tf) == "PROMISING BUT UNPROVEN"
