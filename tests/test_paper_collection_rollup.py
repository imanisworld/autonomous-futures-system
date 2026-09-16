from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from scripts import paper_collection_rollup as rollup


def _scanner_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE scans (id INTEGER PRIMARY KEY, timestamp TEXT);
        CREATE TABLE options_shadow_journal (id INTEGER PRIMARY KEY, timestamp TEXT, status TEXT);
        CREATE TABLE options_episode_blocks (id INTEGER PRIMARY KEY, blocked_at TEXT);
        """
    )
    conn.execute("INSERT INTO scans VALUES (1, '2026-09-16T14:00:00+00:00')")
    conn.execute("INSERT INTO scans VALUES (2, '2026-09-17T14:00:00+00:00')")
    conn.execute("INSERT INTO options_shadow_journal VALUES (1, '2026-09-16T15:00:00+00:00', 'ACTIVE')")
    conn.execute("INSERT INTO options_shadow_journal VALUES (2, '2026-09-16T16:00:00+00:00', 'INVALIDATED')")
    conn.execute("INSERT INTO options_episode_blocks VALUES (1, '2026-09-16T17:00:00+00:00')")
    conn.commit()
    conn.close()


def _coverage_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE coverage_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            observer_version TEXT,
            session_date TEXT,
            symbols_requested INTEGER,
            symbols_observable INTEGER,
            events INTEGER
        );
        CREATE TABLE coverage_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_date TEXT,
            sequence TEXT
        );
        """
    )
    # Two runs for the same session: only the latest run is surfaced.
    conn.execute("INSERT INTO coverage_runs (observer_version,session_date,symbols_requested,symbols_observable,events) VALUES ('cov-v0.1','2026-09-16',20,19,5)")
    conn.execute("INSERT INTO coverage_runs (observer_version,session_date,symbols_requested,symbols_observable,events) VALUES ('cov-v0.1','2026-09-16',20,20,6)")
    conn.executemany(
        "INSERT INTO coverage_events (session_date, sequence) VALUES (?, ?)",
        [
            ("2026-09-16", "strat_212_reversal"),
            ("2026-09-16", "strat_212_reversal"),
            ("2026-09-16", "strat_122"),
            ("2026-09-16", "strat_222_continuation"),
        ],
    )
    conn.commit()
    conn.close()


def test_period_windows_use_new_york_day_boundaries():
    daily = rollup.period_window("daily", date(2026, 9, 16))
    assert daily.start_utc == datetime(2026, 9, 16, 4, 0, tzinfo=timezone.utc)
    assert daily.end_utc == datetime(2026, 9, 17, 4, 0, tzinfo=timezone.utc)
    weekly = rollup.period_window("weekly", date(2026, 9, 16))
    assert weekly.start_date.isoformat() == "2026-09-14"
    assert weekly.end_date.isoformat() == "2026-09-20"


def test_options_scanner_summary_counts_only_period(tmp_path):
    db = tmp_path / "options_scanner.sqlite"
    _scanner_db(db)
    window = rollup.period_window("daily", date(2026, 9, 16))
    result = rollup.options_scanner_summary(db, window)
    assert result["scans"] == 1
    assert result["journal_rows"] == 2
    assert result["episode_blocks"] == 1
    assert result["journal_status"] == {"ACTIVE": 1, "INVALIDATED": 1}


def test_coverage_summary_uses_latest_session_run_and_labels_raw_events(tmp_path):
    db = tmp_path / "coverage.sqlite"
    _coverage_db(db)
    window = rollup.period_window("daily", date(2026, 9, 16))
    result = rollup.coverage_summary(db, window)
    assert result["sessions"] == 1
    assert result["runs"][0]["symbols_observable"] == 20
    assert result["events"] == 4
    assert result["by_sequence"]["strat_212_reversal"] == 2
    assert result["prospective_raw"] == {"strat_122": 1, "strat_212_reversal": 2}
    assert "NOT independent validation episodes" in result["note"]


def test_jsonl_inventory_finds_new_evidence_streams(tmp_path):
    path = tmp_path / "asia_d_ema_cohort" / "evidence.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"timestamp":"2026-09-16T22:15:00Z","record_type":"BAR"}\n'
        '{"timestamp":"2026-09-16T22:30:00Z","record_type":"CANDIDATE"}\n'
    )
    window = rollup.period_window("daily", date(2026, 9, 16))
    rows = rollup.changed_jsonl_streams(tmp_path, window)
    assert rows == [{
        "path": "asia_d_ema_cohort/evidence.jsonl",
        "period_rows": 2,
        "total_rows": 2,
        "last": "2026-09-16T22:30:00+00:00",
    }]


def test_reports_keep_options_validation_language_honest():
    census = {
        "collectors": [
            {"name": "options scans", "status": "FRESH"},
            {"name": "options shadow journal", "status": "FRESH"},
            {"name": "options companion", "status": "FRESH"},
        ]
    }
    scanner = {"scans": 20, "journal_rows": 2, "episode_blocks": 1, "journal_status": {"ACTIVE": 1}}
    coverage = {
        "sessions": 1,
        "events": 8,
        "runs": [{"session_date": "2026-09-16", "symbols_observable": 20, "symbols_requested": 20}],
        "prospective_raw": {"strat_212_reversal": 3, "strat_122": 2},
    }
    window = rollup.period_window("daily", date(2026, 9, 16))
    text = rollup.build_options_report(census, scanner, coverage, [], window, "daily")
    assert "raw events only" in text
    assert "2-1-2 reversal=3" in text
    assert "1-2-2=2" in text
    assert "validated" not in text.lower()


def test_reporter_has_no_trading_or_broker_dependency_and_units_are_oneshot():
    root = Path(__file__).resolve().parents[1]
    source = (root / "scripts" / "paper_collection_rollup.py").read_text()
    for forbidden in ("tradovate", "PaperBroker", "RiskEngine", "DecisionEngine", "execute_bracket"):
        assert forbidden not in source
    assert rollup.FUTURES_WEBHOOK_ENV == "DISCORD_ROUTE_PAPER_COLLECTION_FUTURES"
    assert rollup.OPTIONS_WEBHOOK_ENV == "DISCORD_ROUTE_PAPER_COLLECTION_OPTIONS"
    for name in ("daily", "weekly"):
        service = (root / "deploy" / "systemd" / f"afs-paper-collection-{name}.service").read_text()
        timer = (root / "deploy" / "systemd" / f"afs-paper-collection-{name}.timer").read_text()
        assert "Type=oneshot" in service
        assert "scripts.paper_collection_rollup" in service
        assert "systemctl" not in service and "git " not in service
        assert "Persistent=true" in timer
