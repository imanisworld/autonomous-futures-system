from __future__ import annotations

import json

import pytest

from research.range_break_semantics_audit import (
    BREAK,
    BREAK_REPEAT,
    NO_DATA,
    build_report,
    one_shot_break_events,
    raw_break_events,
    read_journal_rows,
)


def _wall(name: str, kind: str, value: float, *, fresh: bool = True) -> dict:
    return {
        "name": name,
        "kind": kind,
        "source": "price",
        "value": value,
        "fresh": fresh,
    }


def _row(
    ts: str,
    *,
    instrument: str = "MES",
    signal_type: str = BREAK,
    direction: str = "LONG",
    entry: float | None = 5930.0,
    stop: float | None = 5914.08,
    target: float | None = 5950.0,
    wall_value: float = 5920.0,
    wall_fresh: bool = True,
) -> dict:
    signal = {
        "signal_type": signal_type,
        "direction": direction,
        "entry_candidate": entry,
        "stop_candidate": stop,
        "target_candidate": target,
    }
    if direction == "LONG":
        above = [_wall("PDH", "resistance", 5950.0)]
        below = [_wall("ORB_HIGH", "resistance", wall_value, fresh=wall_fresh)]
    else:
        above = [_wall("ORB_LOW", "support", wall_value, fresh=wall_fresh)]
        below = [_wall("PWL", "support", 5850.0)]
    return {
        "ts": ts,
        "instrument": instrument,
        "decision": "NO_TRADE",
        "range_signal": signal,
        "wall_context": {
            "walls_above": above,
            "walls_below": below,
        },
    }


def _outcome(row: dict, result: str, pnl_ticks: float) -> dict:
    signal = row["range_signal"]
    key = (
        f"range_signal|{row['instrument']}|{row['ts']}|range_break_close|"
        f"{signal['direction']}|{float(signal['entry_candidate'])}"
    )
    return {
        "type": "SHADOW_OUTCOME",
        "lane": "range_signal",
        "candidate_key": key,
        "shadow_outcome": {"result": result, "pnl_ticks": pnl_ticks, "final": True},
    }


def test_parses_break_distance_wall_and_outcome():
    row = _row("2026-08-01T10:00:00+00:00")
    events = raw_break_events([row, _outcome(row, "WIN", 8.0)])
    assert len(events) == 1
    event = events[0]
    assert event.wall_name == "ORB_HIGH"
    assert event.wall_fresh is True
    assert event.wall_price == pytest.approx(5920.0)
    assert event.break_pct == pytest.approx((5930.0 - 5920.0) / 5920.0)
    assert event.outcome_result == "WIN"
    assert event.pnl_ticks == pytest.approx(8.0)


def test_short_stop_formula_recovers_broken_support():
    # 5880 * 1.001 = 5885.88
    row = _row(
        "2026-08-01T10:00:00+00:00",
        direction="SHORT",
        entry=5870.0,
        stop=5885.88,
        target=5850.0,
        wall_value=5880.0,
        wall_fresh=False,
    )
    event = raw_break_events([row])[0]
    assert event.wall_name == "ORB_LOW"
    assert event.wall_fresh is False
    assert event.break_pct == pytest.approx((5880.0 - 5870.0) / 5880.0)


def test_one_shot_skips_same_break_until_non_break_rearms():
    first = _row("2026-08-01T10:00:00+00:00")
    repeat = _row("2026-08-01T10:15:00+00:00", entry=5940.0, target=5960.0)
    inside = _row(
        "2026-08-01T10:30:00+00:00",
        signal_type="RANGE_MIDDLE_NO_TRADE",
        direction="NONE",
        entry=None,
        stop=None,
        target=None,
    )
    again = _row("2026-08-01T10:45:00+00:00")
    events = one_shot_break_events([first, repeat, inside, again])
    assert [e.ts for e in events] == [first["ts"], again["ts"]]


def test_repeat_marker_and_no_data_preserve_arm():
    first = _row("2026-08-01T10:00:00+00:00")
    repeat_marker = _row(
        "2026-08-01T10:15:00+00:00",
        signal_type=BREAK_REPEAT,
        entry=None,
        stop=None,
        target=None,
    )
    no_data = _row(
        "2026-08-01T10:30:00+00:00",
        signal_type=NO_DATA,
        direction="NONE",
        entry=None,
        stop=None,
        target=None,
    )
    old_style_repeat = _row("2026-08-01T10:45:00+00:00", entry=5940.0, target=5960.0)
    events = one_shot_break_events([first, repeat_marker, no_data, old_style_repeat])
    assert len(events) == 1
    assert events[0].ts == first["ts"]


def test_different_wall_is_new_one_shot_event():
    first = _row("2026-08-01T10:00:00+00:00")
    # New broken wall 5950 -> unchanged LONG stop formula 5944.05.
    second = _row(
        "2026-08-01T10:15:00+00:00",
        entry=5965.0,
        stop=5944.05,
        target=5990.0,
        wall_value=5950.0,
    )
    second["wall_context"]["walls_below"][0]["name"] = "PDH"
    events = one_shot_break_events([first, second])
    assert len(events) == 2
    assert [e.wall_name for e in events] == ["ORB_HIGH", "PDH"]


def test_report_separates_freshness_without_turning_it_into_a_gate():
    fresh = _row("2026-08-01T10:00:00+00:00", wall_fresh=True)
    stale = _row(
        "2026-08-01T11:00:00+00:00",
        wall_fresh=False,
        wall_value=5940.0,
        entry=5950.0,
        stop=5934.06,
        target=5970.0,
    )
    stale["wall_context"]["walls_below"][0]["name"] = "CALL_WALL"
    # Force a re-arm between independent events.
    clear = _row(
        "2026-08-01T10:30:00+00:00",
        signal_type="RANGE_REJECT",
        direction="SHORT",
        entry=None,
        stop=None,
        target=None,
    )
    rows = [fresh, _outcome(fresh, "WIN", 10), clear, stale, _outcome(stale, "LOSS", -12)]
    report = build_report(rows)
    assert report["policy_changes"] == []
    assert report["by_wall_freshness"]["fresh"]["resolved_win_loss"] == 1
    assert report["by_wall_freshness"]["stale"]["resolved_win_loss"] == 1
    assert report["by_wall_freshness"]["fresh"]["win_rate"] == 1.0
    assert report["by_wall_freshness"]["stale"]["win_rate"] == 0.0


def test_report_distance_quantiles_are_descriptive():
    first = _row("2026-08-01T10:00:00+00:00", entry=5930.0)
    clear = _row(
        "2026-08-01T10:15:00+00:00",
        signal_type="RANGE_MIDDLE_NO_TRADE",
        direction="NONE",
        entry=None,
        stop=None,
        target=None,
    )
    second = _row(
        "2026-08-01T10:30:00+00:00",
        wall_value=5920.0,
        entry=5980.0,
        stop=5914.08,
        target=6000.0,
    )
    report = build_report([first, clear, second])
    distance = report["distance"]
    assert distance["known"] == 2
    assert distance["fraction_quantiles"]["max"] == pytest.approx((5980 - 5920) / 5920)
    assert any("No maximum break distance" in caveat for caveat in report["caveats"])


def test_missing_wall_context_is_unknown_not_crash():
    row = _row("2026-08-01T10:00:00+00:00")
    row.pop("wall_context")
    event = raw_break_events([row])[0]
    assert event.wall_name is None
    assert event.wall_fresh is None
    assert event.break_pct is None


def test_read_journal_rows_skips_malformed_lines(tmp_path):
    path = tmp_path / "journal_2026-08-01.jsonl"
    good = _row("2026-08-01T10:00:00+00:00")
    path.write_text(json.dumps(good) + "\nnot-json\n[]\n", encoding="utf-8")
    rows = read_journal_rows(tmp_path)
    assert rows == [good]
