from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from scripts.post_cap_shadow_report import build_report, collect_post_cap_rows


DAY = date(2026, 10, 5)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _blocked(ts: str, *, direction: str, entry: float, stop: float, target: float) -> dict:
    return {
        "timestamp": ts,
        "instrument": "MNQ",
        "session": "new_york",
        "decision": "BLOCKED_MAX_TRADES",
        "observed_decision": "TRADE",
        "reason": "Daily trade capacity reached; setup observed, execution blocked.",
        "execution_block": {
            "code": "BLOCKED_MAX_TRADES",
            "trade_count": 3,
            "limit": 3,
        },
        "timeframe_minutes": 15,
        "setup": {
            "direction": direction,
            "entry": entry,
            "stop": stop,
            "target": target,
            "rr_ratio": 2.0,
            "strategy": "orb_reclaim",
        },
    }


def test_collects_all_post_cap_setups_and_numbers_from_four(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    rows = [
        _blocked(
            "2026-10-05T14:00:00+00:00",
            direction="LONG",
            entry=20000.0,
            stop=19998.0,
            target=20004.0,
        ),
        {
            "timestamp": "2026-10-05T14:05:00+00:00",
            "instrument": "MNQ",
            "decision": "BLOCKED_MAX_TRADES",
            "observed_decision": "NO_TRADE",
            "execution_block": {"code": "BLOCKED_MAX_TRADES", "trade_count": 3, "limit": 3},
            "setup": None,
        },
        _blocked(
            "2026-10-05T14:15:00+00:00",
            direction="SHORT",
            entry=20010.0,
            stop=20012.0,
            target=20006.0,
        ),
    ]
    _write_jsonl(log_dir / f"journal_{DAY.isoformat()}.jsonl", rows)

    found = collect_post_cap_rows(log_dir, requested_date=DAY, expected_cap=3)

    assert [row["opportunity_number"] for row in found] == [4, 5]
    assert [row["post_cap_index"] for row in found] == [1, 2]
    assert all(row["source_decision"] == "BLOCKED_MAX_TRADES" for row in found)


def test_report_resolves_post_cap_four_and_five_without_execution(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    _write_jsonl(
        log_dir / f"journal_{DAY.isoformat()}.jsonl",
        [
            _blocked(
                "2026-10-05T14:00:00+00:00",
                direction="LONG",
                entry=20000.0,
                stop=19998.0,
                target=20004.0,
            ),
            _blocked(
                "2026-10-05T14:15:00+00:00",
                direction="SHORT",
                entry=20010.0,
                stop=20012.0,
                target=20006.0,
            ),
        ],
    )
    _write_jsonl(
        log_dir / f"bars_MNQ_{DAY.isoformat()}.jsonl",
        [
            {
                "ts": "2026-10-05T14:15:00+00:00",
                "open": 20000.0,
                "high": 20004.0,
                "low": 20000.0,
                "close": 20003.0,
                "timeframe": "15",
            },
            {
                "ts": "2026-10-05T14:30:00+00:00",
                "open": 20010.0,
                "high": 20012.0,
                "low": 20009.0,
                "close": 20011.0,
                "timeframe": "15",
            },
        ],
    )

    report = build_report(log_dir, requested_date=DAY, expected_cap=3)

    assert report["read_only"] is True
    assert report["execution_authority"] is False
    assert report["collection_policy"] == "ALL_POST_CAP_SELECTED_SETUPS"
    assert [row["opportunity_number"] for row in report["rows"]] == [4, 5]
    assert [row["evidence_status"] for row in report["rows"]] == ["FINAL", "FINAL"]
    assert report["rows"][0]["outcome"]["result"] == "TARGET_HIT"
    assert report["rows"][1]["outcome"]["result"] == "STOP_HIT"
    assert report["summary"]["4"]["wins"] == 1
    assert report["summary"]["5"]["losses"] == 1
    assert report["summary"]["all_post_cap"]["filled_terminal"] == 2


def test_same_bar_stop_and_target_is_pessimistic_stop(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    _write_jsonl(
        log_dir / f"journal_{DAY.isoformat()}.jsonl",
        [
            _blocked(
                "2026-10-05T14:00:00+00:00",
                direction="LONG",
                entry=20000.0,
                stop=19998.0,
                target=20004.0,
            )
        ],
    )
    _write_jsonl(
        log_dir / f"bars_MNQ_{DAY.isoformat()}.jsonl",
        [
            {
                "ts": "2026-10-05T14:15:00+00:00",
                "open": 20000.0,
                "high": 20005.0,
                "low": 19997.0,
                "close": 20001.0,
                "timeframe": "15",
            }
        ],
    )

    report = build_report(log_dir, requested_date=DAY)
    outcome = report["rows"][0]["outcome"]
    assert outcome["result"] == "STOP_HIT"
    assert outcome["pessimistic_same_bar"] is True


def test_pending_is_not_counted_as_final(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    _write_jsonl(
        log_dir / f"journal_{DAY.isoformat()}.jsonl",
        [
            _blocked(
                "2026-10-05T14:00:00+00:00",
                direction="LONG",
                entry=20000.0,
                stop=19998.0,
                target=20004.0,
            )
        ],
    )
    _write_jsonl(
        log_dir / f"bars_MNQ_{DAY.isoformat()}.jsonl",
        [
            {
                "ts": "2026-10-05T14:15:00+00:00",
                "open": 19990.0,
                "high": 19995.0,
                "low": 19989.0,
                "close": 19994.0,
                "timeframe": "15",
            }
        ],
    )

    report = build_report(log_dir, requested_date=DAY)
    row = report["rows"][0]
    assert row["evidence_status"] == "PENDING"
    assert report["summary"]["all_post_cap"]["final"] == 0
    assert report["summary"]["all_post_cap"]["pending"] == 1


def test_cap_mismatch_fails_closed(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    row = _blocked(
        "2026-10-05T14:00:00+00:00",
        direction="LONG",
        entry=20000.0,
        stop=19998.0,
        target=20004.0,
    )
    row["execution_block"]["limit"] = 4
    _write_jsonl(log_dir / f"journal_{DAY.isoformat()}.jsonl", [row])

    with pytest.raises(ValueError, match="cap mismatch"):
        collect_post_cap_rows(log_dir, requested_date=DAY, expected_cap=3)
