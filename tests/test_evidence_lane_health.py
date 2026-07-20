from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from execution.mes_trend_consolidation_break_evidence import evidence_path as mes_path
from execution.mnq_strat_evidence import evidence_path as mnq_path, state_path as mnq_state
from ops.evidence_lane_health import build_snapshot, format_snapshot


NOW = datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc)


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _fresh_bars(log_dir: Path) -> None:
    for instrument in ("MES", "MNQ"):
        path = log_dir / f"bars_{instrument}_2026-07-20.jsonl"
        path.write_text(json.dumps({
            "time": (NOW - timedelta(minutes=15)).isoformat(),
            "timeframe": "15",
        }) + "\n")


def _lane(snapshot: dict, name: str) -> dict:
    return next(item for item in snapshot["lanes"] if item["lane"] == name)


def test_fresh_feed_and_no_patterns_is_quiet_not_starved(tmp_path):
    _fresh_bars(tmp_path)
    snapshot = build_snapshot(tmp_path, now=NOW)
    assert snapshot["overall_status"] == "QUIET"
    assert all(item["status"] == "QUIET" for item in snapshot["lanes"])
    assert all(item["signals"] == ["NO_PATTERN_MATCHES"] for item in snapshot["lanes"])


def test_rolls_up_daily_counts_and_surfaces_rejection_block(tmp_path, monkeypatch):
    _fresh_bars(tmp_path)
    monkeypatch.setenv("MES_TREND_CONSOLIDATION_BREAK_MODE", "paper_sim")
    _write_rows(mes_path(tmp_path), [
        {
            "event": "CANDIDATE", "timestamp": "2026-07-20T13:30:00+00:00",
            "accepted": True, "fill_status": "PENDING",
        },
        {
            "event": "FILL", "entry_ts": "2026-07-20T13:45:00+00:00",
        },
        {
            "event": "OUTCOME", "exit_ts": "2026-07-20T14:00:00+00:00",
            "entry_ts": "2026-07-20T13:45:00+00:00", "result": "WIN",
            "net_dollars": 10, "net_ticks": 8,
        },
    ])
    _write_rows(mnq_path(tmp_path, "strat_22_reversal"), [{
        "event": "CANDIDATE", "timestamp": "2026-07-20T13:30:00+00:00",
        "accepted": False, "fill_status": "NO_FILL",
        "rejection_reason": "STRUCTURAL_ISOLATION_UNCONFIRMED_FAIL_CLOSED",
    }])

    snapshot = build_snapshot(tmp_path, now=NOW)
    mes = _lane(snapshot, "trend_consolidation_break")
    mnq = _lane(snapshot, "strat_22_reversal")
    assert mes["counts"]["fills"] == 1
    assert mes["counts"]["outcomes"] == 1
    assert mes["counts"]["wins"] == 1
    assert mnq["status"] == "BLOCKED"
    assert "ALL_CANDIDATES_REJECTED" in mnq["signals"]
    assert snapshot["totals"]["candidates"] == 2
    assert snapshot["overall_status"] == "BLOCKED"


def test_no_fill_breakdown_separates_observe_only_from_terminal_paper_miss(tmp_path):
    _fresh_bars(tmp_path)
    _write_rows(mes_path(tmp_path), [
        {
            "event": "CANDIDATE", "timestamp": "2026-07-20T13:15:00+00:00",
            "accepted": True, "mode": "observe_only", "fill_status": "NO_FILL",
        },
        {
            "event": "NO_FILL", "resolved_at": "2026-07-20T13:30:00+00:00",
            "reason": "ENTRY_NOT_TRIGGERED",
        },
    ])

    counts = _lane(build_snapshot(tmp_path, now=NOW), "trend_consolidation_break")["counts"]

    assert counts["no_fills"] == 2
    assert counts["observe_only_no_orders"] == 1
    assert counts["terminal_no_fills"] == 1


def test_paper_candidate_without_fill_is_starved_but_open_state_is_in_flight(
    tmp_path, monkeypatch
):
    _fresh_bars(tmp_path)
    monkeypatch.setenv("MNQ_STRAT_22_REVERSAL_MODE", "paper_sim")
    row = {
        "event": "CANDIDATE", "timestamp": "2026-07-20T13:30:00+00:00",
        "accepted": True, "fill_status": "NO_FILL",
    }
    _write_rows(mnq_path(tmp_path, "strat_22_reversal"), [row])
    snapshot = build_snapshot(tmp_path, now=NOW)
    assert _lane(snapshot, "strat_22_reversal")["status"] == "STARVED"

    mnq_state(tmp_path, "strat_22_reversal").write_text(json.dumps({
        "seen": [], "position": {"paper_order_id": "PAPER-1"},
    }))
    row["fill_status"] = "FILLED"
    _write_rows(mnq_path(tmp_path, "strat_22_reversal"), [row])
    snapshot = build_snapshot(tmp_path, now=NOW)
    assert _lane(snapshot, "strat_22_reversal")["status"] == "OPEN"
    assert snapshot["overall_status"] == "IN_FLIGHT"


def test_stale_feed_is_obvious_and_text_view_is_concise(tmp_path):
    snapshot = build_snapshot(tmp_path, now=NOW)
    assert snapshot["overall_status"] == "BLOCKED"
    assert all(item["status"] == "BLOCKED" for item in snapshot["lanes"])
    rendered = format_snapshot(snapshot)
    assert "MES STALE" in rendered and "MNQ STALE" in rendered
    assert "CAND" in rendered and "W/L/BE" in rendered
    assert "FEED_STALE" in rendered


def test_historical_snapshot_does_not_call_old_silence_a_feed_failure(tmp_path):
    snapshot = build_snapshot(tmp_path, day=date(2026, 7, 19), now=NOW)
    assert snapshot["feeds"]["MES"]["status"] == "NOT_EVALUATED"
    assert snapshot["overall_status"] == "QUIET"


def test_sunday_reopen_grace_matches_feed_gap_monitor(tmp_path):
    sunday = datetime(2026, 7, 19, 22, 10, tzinfo=timezone.utc)
    snapshot = build_snapshot(tmp_path, now=sunday)
    assert snapshot["feeds"]["MES"]["status"] == "FRESH"
    assert snapshot["feeds"]["MNQ"]["status"] == "FRESH"
    assert snapshot["overall_status"] == "QUIET"
