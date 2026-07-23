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
    assert v["problems"] == ["service unreachable"]


def test_health_auth_and_disk_and_errors():
    v = hd.evaluate_health({
        "service_ok": True, "broker_reachable": True, "position_flat": True,
        "auth_state": "OUTAGE", "errors_today": 3, "disk_pct": 92.0,
    })
    assert v["status"] == "ALERT"
    assert any("auth OUTAGE" in p for p in v["problems"])
    assert any("disk 92% full" in p for p in v["problems"])
    assert any("3 real error" in p for p in v["problems"])


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
    assert any("position OPEN" in n for n in v["notes"])


def test_health_format_contains_status_icon():
    checks = {"service_ok": True, "broker_reachable": True, "position_flat": True,
              "auth_state": "HEALTHY", "errors_today": 0, "disk_pct": 17.0}
    text = hd.format_digest(hd.evaluate_health(checks), checks, day_iso="2026-06-27")
    assert "Box health — 2026-06-27: OK" in text
    assert "\U0001F7E2" in text


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
