"""
tests/test_feed_watchdog.py

Covers the ingestion dead-man's-switch (scripts/feed_watchdog.py): it must alert
exactly once when the feed goes stale during an active session, stay quiet
between reminders, send a recovery notice, and never alert outside a session.
Regression guard for the 2026-06-04 all-day silent ingestion outage.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from zoneinfo import ZoneInfo

from scripts import feed_watchdog as fw

_ET = ZoneInfo("America/New_York")


def _cfg(tmp_path: Path):
    return SimpleNamespace(
        log_dir=str(tmp_path),
        expected_timeframe_minutes=15,
        discord_webhook_url="https://discord.test/hook",
    )


def _write_latest(tmp_path: Path, received_at: datetime) -> None:
    (tmp_path / "latest_webhook.json").write_text(
        json.dumps({"received_at": received_at.isoformat()}), encoding="utf-8"
    )


class _Capture:
    def __init__(self):
        self.messages: list[str] = []

    def __call__(self, cfg, content):
        self.messages.append(content)
        return SimpleNamespace(sent=True, reason="sent")


# A Monday 10:00 ET instant inside an active futures session.
_ACTIVE = datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc)  # 10:00 ET


def test_alerts_when_stale_during_active_session(tmp_path):
    _write_latest(tmp_path, _ACTIVE.astimezone(timezone.utc).replace(hour=12))  # ~2h old
    cap = _Capture()
    out = fw.run(now=_ACTIVE, send=cap, config=_cfg(tmp_path))
    assert out["action"] == "alerted"
    assert len(cap.messages) == 1
    assert "INGESTION STALE" in cap.messages[0]
    assert "15m" in cap.messages[0]
    # State persisted as down.
    state = json.loads((tmp_path / "feed_watchdog_state.json").read_text())
    assert state["status"] == "down"


def test_fresh_feed_does_not_alert(tmp_path):
    _write_latest(tmp_path, _ACTIVE)  # 0m old
    cap = _Capture()
    out = fw.run(now=_ACTIVE, send=cap, config=_cfg(tmp_path))
    assert out["action"] == "ok"
    assert cap.messages == []


def test_no_alert_outside_session(tmp_path):
    # Saturday — futures closed; even a very stale feed must not alert.
    saturday = datetime(2026, 6, 6, 14, 0, tzinfo=timezone.utc)
    _write_latest(tmp_path, datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc))
    cap = _Capture()
    out = fw.run(now=saturday, send=cap, config=_cfg(tmp_path))
    assert out["action"] == "idle_session"
    assert cap.messages == []


def test_no_duplicate_alert_before_reminder_window(tmp_path):
    _write_latest(tmp_path, datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc))
    cfg = _cfg(tmp_path)
    cap = _Capture()
    fw.run(now=_ACTIVE, send=cap, config=cfg)            # first → alert
    out = fw.run(now=_ACTIVE, send=cap, config=cfg)      # immediate re-run → quiet
    assert out["action"] == "still_down_no_reminder"
    assert len(cap.messages) == 1


def test_recovery_notice_after_outage(tmp_path):
    cfg = _cfg(tmp_path)
    cap = _Capture()
    _write_latest(tmp_path, datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc))
    fw.run(now=_ACTIVE, send=cap, config=cfg)            # goes down
    _write_latest(tmp_path, _ACTIVE)                     # fresh again
    out = fw.run(now=_ACTIVE, send=cap, config=cfg)
    assert out["action"] == "recovered"
    assert any("RECOVERED" in m for m in cap.messages)
    state = json.loads((tmp_path / "feed_watchdog_state.json").read_text())
    assert state["status"] == "ok"


def test_no_webhook_file_is_treated_as_stale(tmp_path):
    cap = _Capture()
    out = fw.run(now=_ACTIVE, send=cap, config=_cfg(tmp_path))  # no latest_webhook.json
    assert out["action"] == "alerted"
    assert "no webhook on record" in cap.messages[0]
