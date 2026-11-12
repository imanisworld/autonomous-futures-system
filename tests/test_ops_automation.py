"""Tests for scripts/health_digest.py and scripts/backup_proof_data.py."""

from __future__ import annotations

import sqlite3
import json
from datetime import date

from scripts import backup_proof_data as bk
from scripts import health_digest as hd


# ── health_digest ──────────────────────────────────────────────────────────
def test_health_all_green():
    v = hd.evaluate_health({
        "service_ok": True, "broker_reachable": True, "position_flat": True,
        "auth_state": "HEALTHY", "errors_today": 0, "disk_pct": 17.0,
    })
    assert v["status"] == "OK" and v["problems"] == []


def test_health_service_down_is_alert_and_short_circuits():
    v = hd.evaluate_health({"service_ok": False, "errors_today": 5, "disk_pct": 99})
    assert v["status"] == "ALERT"
    assert v["problems"] == ["trading bot is not answering"]


def test_health_auth_and_disk_and_errors():
    v = hd.evaluate_health({
        "service_ok": True, "broker_reachable": True, "position_flat": True,
        "auth_state": "OUTAGE", "errors_today": 3, "disk_pct": 92.0,
    })
    assert v["status"] == "ALERT"
    assert any("broker login: down" in p for p in v["problems"])
    assert any("server disk is 92% full" in p for p in v["problems"])
    assert any("3 errors in today's log" in p for p in v["problems"])


def test_health_disk_warn_band_and_open_position_note():
    # A BRACKETED open position (working orders present) is a note, not an
    # alarm. (An open position with 0 working orders is NAKED → ALERT, and an
    # unknown protection state → WARN — see tests/test_health_digest_verdict.py;
    # changed after the 2026-07-21 MES orphan sat naked ~36h behind an "OK".)
    v = hd.evaluate_health({
        "service_ok": True, "broker_reachable": True, "position_flat": False,
        "auth_state": "HEALTHY", "errors_today": 0, "disk_pct": 83.0,
        "working_orders": 2,
    })
    assert v["status"] == "WARN"            # disk in warn band
    assert any("a position is open" in n for n in v["notes"])


def test_health_format_contains_status_icon():
    checks = {"service_ok": True, "broker_reachable": True, "position_flat": True,
              "auth_state": "HEALTHY", "errors_today": 0, "disk_pct": 17.0}
    text = hd.format_digest(hd.evaluate_health(checks), checks, day_iso="2026-06-27")
    assert "Server health · Sat Jun 27" in text
    assert "Overall: all good" in text
    assert "Broker login: working" in text
    assert "Open positions: none" in text
    assert text.startswith("\U0001F7E2")
    # Plain English: no ISO dates, no status enums, no "Box".
    for jargon in ("2026-06-27", "HEALTHY", "Box", "flat"):
        assert jargon not in text


def test_health_format_alert_reads_urgent_in_plain_words():
    checks = {"service_ok": True, "broker_reachable": True, "position_flat": False,
              "auth_state": "OUTAGE", "errors_today": 0, "disk_pct": 17.0,
              "working_orders": 0, "bars_without_decisions": True}
    text = hd.format_digest(hd.evaluate_health(checks), checks, day_iso="2026-06-27")
    assert text.startswith("\U0001F534")
    assert "Overall: PROBLEM — needs attention now" in text
    assert "- OPEN POSITION WITH NO STOP-LOSS — check Tradovate now" in text
    assert "- broker login: down" in text
    assert "- prices are arriving but no setups are being checked" in text
    for jargon in ("NAKED", "OUTAGE", "pipeline blind"):
        assert jargon not in text


# ── backup_proof_data ──────────────────────────────────────────────────────
def test_files_to_copy_detects_new_and_changed():
    src = {"journal_2026-06-26.jsonl": 100, "journal_2026-06-27.jsonl": 50, "x": 10}
    dst = {"journal_2026-06-26.jsonl": 100, "journal_2026-06-27.jsonl": 40}  # 27 grew, x missing
    assert bk.files_to_copy(src, dst) == ["journal_2026-06-27.jsonl", "x"]


def test_expired_snapshots_by_age():
    names = [
        "options_companion.2026-01-01.sqlite",  # old
        "options_companion.2026-06-27.sqlite",  # today
        "not_a_snapshot.txt",
    ]
    exp = bk.expired_snapshots(names, date(2026, 6, 27), keep_days=120)
    assert exp == ["options_companion.2026-01-01.sqlite"]


def test_backup_main_failsoft(tmp_path, monkeypatch):
    src = tmp_path / "logs"
    src.mkdir()
    (src / "journal_2026-06-27.jsonl").write_text('{"x":1}\n')
    db = src / "options_companion.sqlite"
    conn = sqlite3.connect(db); conn.execute("CREATE TABLE t (a)"); conn.commit(); conn.close()
    dest = tmp_path / "backups"
    monkeypatch.setenv("LOG_DIR", str(src))
    monkeypatch.setenv("PROOF_BACKUP_DIR", str(dest))

    assert bk.main() == 0
    assert (dest / "journal_2026-06-27.jsonl").exists()
    snaps = list((dest / "snapshots").glob("options_companion.*.sqlite"))
    assert len(snaps) == 1
    receipt = json.loads((dest / "backup_proof_data_latest.json").read_text())
    assert receipt["job"] == "backup_proof_data"
    assert receipt["evidence_dir"] == str(dest)
    # idempotent: re-run copies nothing new (same size)
    assert bk.main() == 0


def test_health_main_writes_fail_soft_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    monkeypatch.delenv("DISCORD_ROUTE_HEARTBEAT", raising=False)
    monkeypatch.delenv("DISCORD_ROUTE_DAILY_REPORT", raising=False)
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(hd, "collect", lambda: {
        "service_ok": True, "broker_reachable": True, "position_flat": True,
        "auth_state": "HEALTHY", "errors_today": 0, "disk_pct": 17.0,
    })

    assert hd.main() == 0
    receipt = json.loads((tmp_path / "health_digest_latest.json").read_text())
    assert receipt["job"] == "health_digest"
    assert receipt["verdict"] == "OK"


def _small_sqlite(path, value):
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS t (value TEXT)")
        conn.execute("INSERT INTO t VALUES (?)", (value,))
        conn.commit()
    finally:
        conn.close()


def test_backup_keeps_latest_two_snapshots_per_ledger(tmp_path, monkeypatch):
    from pathlib import Path
    src = tmp_path / "logs"
    src.mkdir()
    _small_sqlite(src / "options_scanner.sqlite", "new")
    _small_sqlite(src / "options_companion.sqlite", "new")
    dest = tmp_path / "backup"
    snap = dest / "snapshots"
    snap.mkdir(parents=True)
    for day in ("2026-11-07", "2026-11-08", "2026-11-09"):
        _small_sqlite(snap / f"options_scanner.{day}.sqlite", day)
        _small_sqlite(snap / f"options_companion.{day}.sqlite", day)
    stray = snap / "old_archive.2026-10-01.sqlite"
    _small_sqlite(stray, "unrelated")
    monkeypatch.setenv("LOG_DIR", str(src))
    monkeypatch.setenv("PROOF_BACKUP_DIR", str(dest))
    monkeypatch.setenv("PROOF_BACKUP_KEEP_SNAPSHOTS", "2")
    monkeypatch.delenv("PROOF_BACKUP_KEEP_DAYS", raising=False)
    monkeypatch.setattr(bk, "_today_utc", lambda: date(2026, 11, 10))
    assert bk.main() == 0
    assert sorted(p.name for p in snap.glob("options_scanner.*.sqlite")) == [
        "options_scanner.2026-11-09.sqlite",
        "options_scanner.2026-11-10.sqlite",
    ]
    assert sorted(p.name for p in snap.glob("options_companion.*.sqlite")) == [
        "options_companion.2026-11-09.sqlite",
        "options_companion.2026-11-10.sqlite",
    ]
    assert stray.is_file()
    for stem in ("options_scanner", "options_companion"):
        target = snap / f"{stem}.2026-11-10.sqlite"
        with sqlite3.connect(f"file:{target}?mode=ro&immutable=1", uri=True) as db:
            assert db.execute("SELECT value FROM t").fetchall() == [("new",)]
            assert db.execute("PRAGMA quick_check").fetchall() == [("ok",)]
    receipt = json.loads((dest / "backup_proof_data_latest.json").read_text())
    assert receipt["status"] == "OK"
    assert receipt["retained_snapshots_per_ledger"] == 2
    assert receipt["ledger_snapshots"] == 2
    assert receipt["pruned"] == 4
    # A same-day re-run should reuse valid daily snapshots, not rewrite them.
    assert bk.main() == 0
    assert json.loads((dest / "backup_proof_data_latest.json").read_text())["ledger_snapshots"] == 0


def test_backup_low_space_fails_before_copy_or_prune(tmp_path, monkeypatch):
    src = tmp_path / "logs"
    src.mkdir()
    _small_sqlite(src / "options_scanner.sqlite", "new")
    dest = tmp_path / "backup"
    snap = dest / "snapshots"
    snap.mkdir(parents=True)
    old = snap / "options_scanner.2026-11-06.sqlite"
    _small_sqlite(old, "retain")
    receipt = dest / "backup_proof_data_latest.json"
    receipt.write_text('{"job":"backup_proof_data","status":"OK"}')
    monkeypatch.setenv("LOG_DIR", str(src))
    monkeypatch.setenv("PROOF_BACKUP_DIR", str(dest))
    monkeypatch.setenv("PROOF_BACKUP_KEEP_SNAPSHOTS", "2")
    monkeypatch.delenv("PROOF_BACKUP_KEEP_DAYS", raising=False)
    monkeypatch.setattr(bk, "_today_utc", lambda: date(2026, 11, 10))
    monkeypatch.setattr(bk, "_disk_free_bytes", lambda _: 0)
    assert bk.main() == 1
    assert old.is_file()
    assert not (snap / "options_scanner.2026-11-10.sqlite").exists()
    assert not receipt.exists()


def test_backup_snapshot_failure_preserves_previous_and_fails(tmp_path, monkeypatch):
    src = tmp_path / "logs"
    src.mkdir()
    _small_sqlite(src / "options_scanner.sqlite", "new")
    dest = tmp_path / "backup"
    snap = dest / "snapshots"
    snap.mkdir(parents=True)
    old = snap / "options_scanner.2026-11-08.sqlite"
    _small_sqlite(old, "old")
    monkeypatch.setenv("LOG_DIR", str(src))
    monkeypatch.setenv("PROOF_BACKUP_DIR", str(dest))
    monkeypatch.setenv("PROOF_BACKUP_KEEP_SNAPSHOTS", "2")
    monkeypatch.delenv("PROOF_BACKUP_KEEP_DAYS", raising=False)
    monkeypatch.setattr(bk, "_today_utc", lambda: date(2026, 11, 10))

    def broken(*_):
        raise OSError("simulated backup I/O failure")

    monkeypatch.setattr(bk, "_snapshot_sqlite_atomic", broken)
    assert bk.main() == 1
    assert old.is_file()
    assert not (dest / "backup_proof_data_latest.json").exists()


def test_backup_legacy_day_retention_and_wrong_count_fail_closed(tmp_path, monkeypatch):
    src = tmp_path / "logs"
    src.mkdir()
    _small_sqlite(src / "options_scanner.sqlite", "new")
    dest = tmp_path / "backup"
    monkeypatch.setenv("LOG_DIR", str(src))
    monkeypatch.setenv("PROOF_BACKUP_DIR", str(dest))
    monkeypatch.setenv("PROOF_BACKUP_KEEP_DAYS", "3")
    assert bk.main() == 1
    assert not dest.exists()
    monkeypatch.delenv("PROOF_BACKUP_KEEP_DAYS")
    monkeypatch.setenv("PROOF_BACKUP_KEEP_SNAPSHOTS", "3")
    assert bk.main() == 1
    assert not dest.exists()


def test_snapshot_candidates_never_touch_unrelated_names():
    names = [
        "options_scanner.2026-11-08.sqlite",
        "options_scanner.2026-11-09.sqlite",
        "options_scanner.2026-11-10.sqlite",
        "options_scanner.2026-11-11.sqlite",
        "options_companion.2026-11-07.sqlite",
        "options_companion.2026-11-10.sqlite",
        "not-a-snapshot",
        "old_archive.2026-01-01.sqlite",
        "options_scanner.9999-99-99.sqlite",
    ]
    assert bk.snapshots_to_prune(names, {"options_scanner", "options_companion"}) == [
        "options_scanner.2026-11-08.sqlite",
        "options_scanner.2026-11-09.sqlite",
    ]


def test_backup_copy_handles_live_uncheckpointed_wal(tmp_path, monkeypatch):
    src = tmp_path / "logs"
    src.mkdir()
    db = src / "options_scanner.sqlite"
    writer = sqlite3.connect(db)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        writer.execute("CREATE TABLE items (value INT)")
        writer.executemany("INSERT INTO items VALUES (?)", [(1,), (2,), (3,)])
        writer.commit()
        # WAL is still live; backup API must see the committed records.
        dest = tmp_path / "backup"
        monkeypatch.setenv("LOG_DIR", str(src))
        monkeypatch.setenv("PROOF_BACKUP_DIR", str(dest))
        monkeypatch.setenv("PROOF_BACKUP_KEEP_SNAPSHOTS", "2")
        monkeypatch.delenv("PROOF_BACKUP_KEEP_DAYS", raising=False)
        assert bk.main() == 0
        backups = list((dest / "snapshots").glob("options_scanner.*.sqlite"))
        assert len(backups) == 1
        with sqlite3.connect(f"file:{backups[0]}?mode=ro&immutable=1", uri=True) as recovered:
            assert recovered.execute("SELECT value FROM items ORDER BY value").fetchall() == [(1,), (2,), (3,)]
    finally:
        writer.close()
