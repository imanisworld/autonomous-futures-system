"""The futures non-Strat loader must feed ``session_vwap`` a PER-BAR price.

The Polygon 5m replay payload's ``vwap`` is already the cumulative RTH session
VWAP. Using it as the per-bar input made ``session_vwap`` volume-average the
running VWAP a second time (a lagged line, not VWAP). The fns-v0.1 prereg says
the payload field is not used; cumulative sum(hlc3*v)/sum(v) is the definition.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from alert_ranker.causal_bars import session_vwap
from research.futures_non_strat_coverage import load_rth_session

OPEN_UTC = datetime(2026, 3, 16, 13, 30, tzinfo=timezone.utc)  # 09:30 ET (EDT)
BARS = [  # (high, low, close, volume)
    (100.0, 98.0, 99.0, 1000.0),
    (104.0, 99.0, 103.0, 3000.0),
    (106.0, 102.0, 105.0, 500.0),
    (105.0, 96.0, 97.0, 4000.0),
]


def _day_file(tmp_path):
    rows, pv, vol = [], 0.0, 0.0
    for i, (h, lo, c, v) in enumerate(BARS):
        pv += (h + lo + c) / 3.0 * v
        vol += v
        rows.append({
            "session": "new_york",
            "timestamp": (OPEN_UTC + timedelta(minutes=5 * i)).isoformat(),
            "open": c, "high": h, "low": lo, "close": c, "volume": v,
            "vwap": pv / vol,  # the payload's cumulative session VWAP
        })
    path = tmp_path / "MNQ_2026-03-16.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path, [r["vwap"] for r in rows]


def test_session_vwap_equals_the_cumulative_rth_vwap_at_every_bar(tmp_path):
    path, payload_cumulative = _day_file(tmp_path)
    bars, _ = load_rth_session(path)
    assert len(bars) == len(BARS)
    for i in range(len(bars)):
        assert session_vwap(bars[: i + 1]) == pytest.approx(payload_cumulative[i], abs=1e-9)


def test_loader_does_not_use_the_payload_vwap_field(tmp_path):
    path, _ = _day_file(tmp_path)
    rows = [json.loads(x) for x in path.read_text().splitlines()]
    for r in rows:
        r["vwap"] = 1.0e9  # garbage: must have no effect
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    bars, _ = load_rth_session(path)
    assert all(b.vwap == pytest.approx((b.high + b.low + b.close) / 3.0) for b in bars)
