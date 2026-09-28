from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ops.weekly_read_surfaces import (
    SurfaceError,
    feed_15m_status,
    forward_campaign_counts,
    futures_trigger_counts,
    options_reclaim_counts,
    project_mgc_counts,
    project_mnq_counts,
)
from scripts import afs_weekly_read


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_futures_counts_drop_prices_and_match_arms(tmp_path: Path):
    _write(
        tmp_path / "tf1m" / "4hr_trigger_evidence_2026-09-26.jsonl",
        [
            {
                "event": "TRIGGER_TOUCH",
                "bar_ts": "2026-09-26T13:31:00+00:00",
                "arm_key": "arm-a",
                "trigger": 21000.0,
                "stop": 20900.0,
                "target": 21200.0,
                "source_state": {"status": "ARMED", "trigger": 21000.0},
            },
            {
                "event": "TRIGGER_BLOCKED",
                "reason": "COMPLETED_1H_STOP_MISSING",
                "bar_ts": "2026-09-26T13:40:00+00:00",
                "trigger": 1,
            },
            {
                "event": "TRIGGER_TOUCH",
                "bar_ts": "2026-09-26T13:45:00+00:00",
                "arm_key": "arm-a",
                "source_state": {"status": "ARMED"},
            },
        ],
    )
    _write(
        tmp_path / "tf1m" / "322_first_live" / "evidence_2026-09-26.jsonl",
        [
            {"event": "ARMED", "bar_ts": "2026-09-26T14:00:00+00:00", "target": 9},
            {"event": "EXPIRED", "reason": "NO_BREAK_BY_11AM", "bar_ts": "2026-09-26T15:00:00+00:00"},
            {"event": "TRIGGER_DUPLICATE", "bar_ts": "2026-09-26T14:10:00+00:00"},
        ],
    )
    (tmp_path / "tf1m" / "322_first_live" / "state_2026-09-26.json").write_text(
        json.dumps(
            {
                "trading_date": "2026-09-26",
                "status": "EXPIRED",
                "invalidation": "NO_BREAK_BY_11AM",
                "trigger": 100,
                "stop": 90,
                "target": 120,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "tf1m" / "notes.txt").write_text("do not read", encoding="utf-8")

    report = futures_trigger_counts(tmp_path)
    assert report["counts"] == {
        "ARMED": 1,
        "TRIGGER_TOUCH": 2,
        "BLOCKED": 1,
        "EXPIRED": 1,
    }
    assert report["dedupe"] == {"duplicate_events": 1, "duplicate_arm_keys": 1}
    assert report["causality"]["flags"]["reason:COMPLETED_1H_STOP_MISSING"] == 1
    assert report["latest_322_state_by_date"] == {"2026-09-26": "EXPIRED"}
    assert "notes.txt" not in " ".join(report["files_read"])
    blob = json.dumps(report)
    assert "21000" not in blob
    assert "20900" not in blob


def test_futures_rejects_path_escape_and_unknown_event(tmp_path: Path):
    outside = tmp_path.parent / "outside.jsonl"
    outside.write_text('{"event":"TRIGGER_TOUCH"}\n', encoding="utf-8")
    link_dir = tmp_path / "tf1m"
    link_dir.mkdir()
    (link_dir / "4hr_trigger_evidence_2026-09-26.jsonl").symlink_to(outside)
    with pytest.raises(SurfaceError, match="escapes the log root"):
        futures_trigger_counts(tmp_path)
    (link_dir / "4hr_trigger_evidence_2026-09-26.jsonl").unlink()

    _write(
        tmp_path / "tf1m" / "4hr_trigger_evidence_2026-09-25.jsonl",
        [{"event": "SYNTHETIC", "bar_ts": "2026-09-25T13:30:00+00:00"}],
    )
    with pytest.raises(SurfaceError, match="unrecognized trigger event"):
        futures_trigger_counts(tmp_path)


def test_forward_projection_is_blind_and_refuses_fetch_without_local_input(tmp_path: Path):
    mnq = {
        "mode": "counts",
        "prereg": "mnq",
        "terminal_portfolio_fills": 3,
        "h1_counts": {"portfolio_fills": 3},
        "pnl": 50,
    }
    with pytest.raises(AssertionError):
        project_mnq_counts(mnq)
    safe = {"mode": "counts", "prereg": "mnq", "h1_counts": {"portfolio_fills": 3}}
    assert project_mnq_counts(safe)["h1_counts"]["portfolio_fills"] == 3
    with pytest.raises(SurfaceError, match="counts mode only"):
        project_mgc_counts({"mode": "look", "terminal_trades": 1})

    blocked = forward_campaign_counts()
    assert blocked["mnq_shared_account"]["reason"] == "local_corpus_required"
    assert blocked["mgc_4h_forward"]["reason"] == "local_counts_required_refusing_fetch"

    path = tmp_path / "mgc-counts.json"
    path.write_text(
        json.dumps({"mode": "counts", "terminal_trades": 4, "observation_days": 2, "net": 1}),
        encoding="utf-8",
    )
    with pytest.raises(AssertionError):
        forward_campaign_counts(mgc_counts=path)


def test_options_reclaim_counts_projects_gate_and_never_looks(tmp_path: Path, monkeypatch):
    import research.options_reclaim_entry as oe

    def _boom(*_args, **_kwargs):
        raise AssertionError("look was invoked")

    monkeypatch.setattr(oe, "look_report", _boom)
    db = tmp_path / "snapshot.sqlite"
    db.write_text("", encoding="utf-8")

    class _Conn:
        def close(self):
            return None

    monkeypatch.setattr(oe, "connect_readonly", lambda _path: _Conn())
    monkeypatch.setattr(oe, "load_episodes", lambda _conn: [])
    monkeypatch.setattr(
        oe,
        "reproduction",
        lambda _eps, _symbols: {"verdict": "PASS", "matched": 1, "total": 1},
    )
    monkeypatch.setattr(oe, "contract_symbols_by_row", lambda _conn: {})
    monkeypatch.setattr(oe, "evaluate", lambda _eps: [{"day": "2026-09-26"}])

    def _counts(_pairs, *, as_of, source):
        assert source == {"db": "snapshot.sqlite"}
        assert as_of == datetime(2026, 9, 26, tzinfo=timezone.utc)
        return {
            "eligible_scorable_pairs": 2,
            "distinct_trading_days_scorable": 1,
            "reclaim_entries_scorable": 1,
            "reclaim_no_entry_scorable": 1,
            "ineligible_by_reason": {},
            "blocked_by_reason": {"reclaim:lineage": 1},
            "gate": {"min_pairs": 50, "min_days": 20, "min_reclaim_entries": 30},
            "status": "COLLECTING",
            "pnl": 12,
        }

    monkeypatch.setattr(oe, "counts_report", _counts)
    report = options_reclaim_counts(db, as_of=datetime(2026, 9, 26, tzinfo=timezone.utc))
    assert report["paired_episodes"] == 2
    assert report["trading_days"] == 1
    assert report["reclaim_entries"] == 1
    assert report["no_entry"] == 1
    assert report["gate_status"] == "COLLECTING"
    assert "pnl" not in json.dumps(report)


def test_options_lineage_failure_skips_evaluation(tmp_path: Path, monkeypatch):
    import research.options_reclaim_entry as oe

    db = tmp_path / "snapshot.sqlite"
    db.write_text("", encoding="utf-8")
    monkeypatch.setattr(oe, "connect_readonly", lambda _path: type("C", (), {"close": lambda self: None})())
    monkeypatch.setattr(oe, "load_episodes", lambda _conn: [])
    monkeypatch.setattr(oe, "contract_symbols_by_row", lambda _conn: {})
    monkeypatch.setattr(
        oe,
        "reproduction",
        lambda *_args: {"verdict": "FAIL", "matched": 0, "total": 2},
    )

    def _no_eval(_eps):
        raise AssertionError("evaluate ran after lineage failure")

    monkeypatch.setattr(oe, "evaluate", _no_eval)
    report = options_reclaim_counts(db)
    assert report["gate_status"] == "LINEAGE_BLOCKED"
    assert report["paired_episodes"] is None
    assert report["lineage_blocks"]["total"] == 2


def test_feed_status_reports_gap_without_prices_or_writes(tmp_path: Path):
    _write(
        tmp_path / "bars_MNQ_2026-09-26.jsonl",
        [
            {"ts": "2026-09-26T13:30:00+00:00", "open": 1, "high": 2, "low": 0, "close": 1, "source": "tv"},
            {"ts": "2026-09-26T13:45:00+00:00", "open": 1, "high": 2, "low": 0, "close": 1},
            {"ts": "2026-09-26T14:15:00+00:00", "open": 9, "close": 9},
        ],
    )
    _write(
        tmp_path / "tf5m" / "bars_MNQ_2026-09-26.jsonl",
        [{"ts": "2026-09-26T14:20:00+00:00", "close": 3}],
    )
    _write(
        tmp_path / "tf1m" / "bars_MES_2026-09-26.jsonl",
        [{"ts": "2026-09-26T14:21:00+00:00", "close": 4}],
    )
    before = {path: path.stat().st_mtime_ns for path in tmp_path.rglob("*") if path.is_file()}
    report = feed_15m_status(tmp_path)
    after = {path: path.stat().st_mtime_ns for path in tmp_path.rglob("*") if path.is_file()}
    assert before == after
    mnq = report["instruments"]["MNQ"]
    assert mnq["last_15m"] == "2026-09-26T14:15:00+00:00"
    assert mnq["first_missing_15m"] == "2026-09-26T14:00:00+00:00"
    assert mnq["last_5m"] == "2026-09-26T14:20:00+00:00"
    assert mnq["producer"] == "tv"
    assert report["instruments"]["MES"]["last_1m"] == "2026-09-26T14:21:00+00:00"
    blob = json.dumps(report)
    assert '"open"' not in blob
    assert '"close"' not in blob


def test_cli_refuses_reclaim_look_flag(tmp_path: Path):
    db = tmp_path / "snapshot.sqlite"
    db.write_text("", encoding="utf-8")
    code = afs_weekly_read.main(["options-reclaim-counts", "--db", str(db), "look"])
    assert code == 2


def test_cli_blocks_poisoned_forward_counts(tmp_path: Path, capsys):
    path = tmp_path / "mgc-counts.json"
    path.write_text(json.dumps({"mode": "counts", "net": 1, "terminal_trades": 1}), encoding="utf-8")
    code = afs_weekly_read.main(["forward-campaign-counts", "--mgc-counts", str(path)])
    assert code == 2
    assert "BLOCKED" in capsys.readouterr().err
