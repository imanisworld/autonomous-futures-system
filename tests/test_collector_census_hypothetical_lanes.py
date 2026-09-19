"""The census must see the isolated hypothetical-ledger lanes and the 5m stream.

Defect proven 2026-09-14 (read-only monitoring audit): the Daily 2-2 lane held an
OPEN MNQ swing for four days and no monitor knew — every freshness check watched
the 15-minute stream only, while all MNQ paper lanes resolve on 5-minute bars,
and no collector census entry existed for any lane.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from ops.collector_census import (
    ABSENT,
    FRESH,
    COLLECTORS,
    build_census,
    format_census,
    hypothetical_lane_positions,
)

NOW = datetime(2026, 9, 14, 10, 5, tzinfo=timezone.utc)
DAY = NOW.strftime("%Y-%m-%d")


def _touch(path, when: datetime, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (when.timestamp(), when.timestamp()))


def _jsonl(rows) -> str:
    return "\n".join(json.dumps(r) for r in rows) + "\n"


def _box_like(tmp_path, *, five_min_bar_at: datetime, d22_position: bool = True):
    """Reproduce the box layout observed on 2026-09-14 10:05Z."""
    ledger = tmp_path / "hypothetical_ledger"
    _touch(
        tmp_path / "tf5m" / f"bars_MNQ_{DAY}.jsonl",
        five_min_bar_at,
        _jsonl([{"ts": five_min_bar_at.isoformat(), "close": 28857.5, "timeframe": "5m"}]),
    )
    state = {
        "balance": 5000.0,
        "epoch": "2026-09-09T04:21:04+00:00",
        "halted": False,
        "peak": 5000.0,
        "position": (
            {
                "direction": "SHORT", "entry": 29338.25, "stop": 29634.25, "target": 28728.25,
                "entry_time": "2026-09-10T11:10:00+00:00", "paper_order_id": "PAPER-38b3",
                "candidate_key": "2026-09-10|DAILY_22_CONTINUATION_FIRST_BREAK|SHORT",
            }
            if d22_position else None
        ),
        "seen": [],
    }
    _touch(ledger / "daily_22_5k" / "swing_state.json", NOW, json.dumps(state))
    _touch(
        ledger / "wide_stop_6k" / "forward_collector_state.json",
        NOW - timedelta(days=3),
        json.dumps({"filled_count": 0, "filled_date": "2026-09-11", "position": None, "seen": []}),
    )
    # wide_stop_4k deliberately absent: it is created lazily on the first candidate.
    _touch(
        ledger / "mes_122_1500" / f"journal_{DAY}.jsonl",
        NOW,
        _jsonl([
            {"ts": "2026-09-14T10:00:00+00:00", "type": "BAR_CLAIM", "instrument": "MES"},
            {"ts": "2026-09-14T10:00:19+00:00", "instrument": "MES", "decision": "NO_TRADE",
             "reason": "Market condition is RANGE_BOUND, not TRENDING."},
        ]),
    )
    return ledger


def test_census_has_heartbeat_collectors_for_the_active_lanes():
    names = {c.name for c in COLLECTORS}
    assert {"bars MNQ 5m", "daily_22 swing state", "mes_122 lane journal"} <= names
    # The wide-stop ledgers have no heartbeat file and must NOT be given a
    # wall-clock DEAD threshold (state is written only on candidates).
    assert not any("wide_stop" in c.name for c in COLLECTORS)


def test_lane_heartbeats_read_fresh_from_a_box_like_tree(tmp_path):
    _box_like(tmp_path, five_min_bar_at=NOW - timedelta(minutes=5))
    census = build_census(tmp_path, NOW)
    by_name = {row["name"]: row for row in census["collectors"]}
    assert by_name["bars MNQ 5m"]["status"] == FRESH
    assert by_name["daily_22 swing state"]["status"] == FRESH
    assert by_name["mes_122 lane journal"]["status"] == FRESH


def test_open_daily_swing_is_reported_with_fresh_bars_not_exposed(tmp_path):
    _box_like(tmp_path, five_min_bar_at=NOW - timedelta(minutes=5))
    lanes = hypothetical_lane_positions(tmp_path, NOW)
    assert lanes["open_positions"] == ["daily_22_5k"]
    d22 = lanes["lanes"]["daily_22_5k"]
    assert d22["open_position"]["direction"] == "SHORT"
    assert d22["open_position"]["entry_time"] == "2026-09-10T11:10:00+00:00"
    assert d22["epoch"] == "2026-09-09T04:21:04+00:00"
    assert lanes["mnq_position_exposed_without_fresh_5m_bars"] is False
    assert lanes["lanes"]["wide_stop_4k"]["exists"] is False
    assert lanes["lanes"]["wide_stop_6k"]["open_position"] is None
    assert lanes["lanes"]["mes_122_1500"]["open_position"] is None


def test_open_daily_swing_with_stale_5m_bars_is_flagged_exposed(tmp_path):
    # The exact false-green route: 15m checks say FRESH, the 5m stream every
    # MNQ lane resolves on has been silent for 45 minutes, a swing is open.
    _box_like(tmp_path, five_min_bar_at=NOW - timedelta(minutes=45))
    lanes = hypothetical_lane_positions(tmp_path, NOW)
    assert lanes["open_positions"] == ["daily_22_5k"]
    assert lanes["five_min_bar_age_minutes"] == 45.0
    assert lanes["mnq_position_exposed_without_fresh_5m_bars"] is True
    rendered = format_census(build_census(tmp_path, NOW))
    assert "MNQ POSITION EXPOSED WITHOUT FRESH 5m BARS" in rendered
    assert "daily_22_5k" in rendered and "OPEN SHORT" in rendered


def test_flat_lanes_with_stale_5m_bars_are_not_exposed(tmp_path):
    _box_like(tmp_path, five_min_bar_at=NOW - timedelta(hours=3), d22_position=False)
    lanes = hypothetical_lane_positions(tmp_path, NOW)
    assert lanes["open_positions"] == []
    assert lanes["mnq_position_exposed_without_fresh_5m_bars"] is False


def test_expected_weekend_carry_is_not_reported_as_stale_bar_exposure(tmp_path):
    _box_like(tmp_path, five_min_bar_at=NOW - timedelta(hours=3), d22_position=True)
    saturday_utc = datetime(2026, 9, 19, 1, 43, tzinfo=timezone.utc)  # Fri 21:43 ET
    lanes = hypothetical_lane_positions(tmp_path, saturday_utc)
    assert lanes["open_positions"] == ["daily_22_5k"]
    assert lanes["mnq_position_exposed_without_fresh_5m_bars"] is False


def test_mes_lane_open_position_comes_from_trade_and_outcome_ids(tmp_path):
    ledger = tmp_path / "hypothetical_ledger" / "mes_122_1500"
    rows = [
        {"ts": "2026-09-14T09:00:05+00:00", "instrument": "MES", "decision": "TRADE",
         "risk_check": {"result": "APPROVED"}, "paper_order_id": "PAPER-1",
         "setup": {"strategy": "strat_122", "direction": "LONG", "entry": 7590.25, "stop": 7585.0, "target": 7605.25}},
        {"ts": "2026-09-14T09:15:05+00:00", "instrument": "MES", "decision": "TRADE",
         "risk_check": {"result": "APPROVED"}, "paper_order_id": "PAPER-2",
         "setup": {"strategy": "strat_122", "direction": "SHORT", "entry": 7600.0, "stop": 7606.0, "target": 7585.0}},
        {"ts": "2026-09-14T09:30:05+00:00", "instrument": "MES", "type": "OUTCOME",
         "outcome": {"result": "LOSS", "paper_order_id": "PAPER-1"}},
    ]
    _touch(ledger / f"journal_{DAY}.jsonl", NOW, _jsonl(rows))
    lanes = hypothetical_lane_positions(tmp_path, NOW)
    mes = lanes["lanes"]["mes_122_1500"]
    assert mes["open_position"]["paper_order_id"] == "PAPER-2"
    assert mes["open_position"]["direction"] == "SHORT"
    assert lanes["open_positions"] == ["mes_122_1500"]
    # A MES swing does not depend on the MNQ 5m stream.
    assert lanes["mnq_position_exposed_without_fresh_5m_bars"] is False


def test_absent_lane_files_report_absent_not_fresh(tmp_path):
    census = build_census(tmp_path, NOW)
    by_name = {row["name"]: row for row in census["collectors"]}
    for name in ("bars MNQ 5m", "daily_22 swing state", "mes_122 lane journal"):
        assert by_name[name]["status"] == ABSENT
    lanes = census["hypothetical_lanes"]
    assert all(row["exists"] is False for row in lanes["lanes"].values())
    assert lanes["open_positions"] == []


def test_census_still_never_writes_with_lanes(tmp_path):
    _box_like(tmp_path, five_min_bar_at=NOW)
    before = sorted(str(p) for p in tmp_path.rglob("*"))
    build_census(tmp_path, NOW)
    assert sorted(str(p) for p in tmp_path.rglob("*")) == before
