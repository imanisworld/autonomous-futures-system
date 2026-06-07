"""
tests/test_htf_lookahead.py

Regression tests for higher-timeframe LOOKAHEAD. HTF rows are timestamped at bar
OPEN; a bar must not be visible to a lower-timeframe decision until it has CLOSED,
otherwise the decision sees future OHLC/direction. These tests fail against the
pre-fix code (which exposed a bar at its open) and pass once _at()/htf_at() apply
a close-delay equal to the bar duration.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from context.htf_loader import HTFLookup, _tf_to_seconds


def _write(p, rows):
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_daily_htf_not_visible_until_closed(tmp_path):
    p = tmp_path / "d.jsonl"
    _write(p, [
        {"unix": 0,      "direction": "UP",   "bias": "UP"},     # day1 open
        {"unix": 86400,  "direction": "DOWN", "bias": "DOWN"},   # day2 open
    ])
    lk = HTFLookup()
    lk.load(p, timeframe="1D")

    def daily_dir(ts):
        c = lk.get_context(datetime.fromtimestamp(ts, tz=timezone.utc), direction="LONG")
        return c.daily_direction if c else None

    # 1h into the still-forming day-2 bar: must still report day-1 (UP).
    assert daily_dir(86400 + 3600) == "UP"
    # 1s before day-2 close: still day-1.
    assert daily_dir(86400 + 86399) == "UP"
    # at day-2 close: day-2 becomes visible.
    assert daily_dir(86400 + 86400) == "DOWN"


def test_4h_htf_not_visible_until_closed(tmp_path):
    p = tmp_path / "h4.jsonl"
    _write(p, [
        {"unix": 0,      "direction": "UP",   "bias": "UP"},
        {"unix": 14400,  "direction": "DOWN", "bias": "DOWN"},
    ])
    lk = HTFLookup()
    lk.load(p, timeframe="4H")

    def fh(ts):
        c = lk.get_context(datetime.fromtimestamp(ts, tz=timezone.utc), direction="LONG")
        return c.four_hour_direction if c else None

    assert fh(14400 + 60) == "UP"        # 1 min into the forming 4H bar
    assert fh(14400 + 14400) == "DOWN"   # at its close


def test_csv_replay_htf_at_one_hour_label_delays():
    """The htf_at helper must delay the '1h'-labelled bars until close (the
    pre-fix duration map keyed on 'one_hour' and silently gave 1h bars 0 delay)."""
    from scripts.csv_to_replay import htf_at
    bars = [
        {"ts": 0,    "label": "1h", "direction": "UP"},
        {"ts": 3600, "label": "1h", "direction": "DOWN"},
    ]
    # 30 min into the still-forming 2nd hour: must return the 1st (closed) bar.
    assert htf_at(bars, 3600 + 1800) is bars[0]
    # at the 2nd bar's close: now visible.
    assert htf_at(bars, 3600 + 3600) is bars[1]


def test_tf_to_seconds_parsing():
    assert _tf_to_seconds("1D") == 86400
    assert _tf_to_seconds("4H") == 14400
    assert _tf_to_seconds("1h") == 3600
    assert _tf_to_seconds("240") == 14400   # bare number = minutes
    assert _tf_to_seconds("60") == 3600
