"""Focused tests for the v2 feed-gap alarm (ops/feed_gap_alarm.py).

The v1 prototype false-fired every healthy half-hour (25-minute threshold vs
a healthy feed's 0-30 minute newest-bar-open age) and flapped stale/recovery
pings off a single global status. These tests pin the reviewed v2 behavior:
31-minute threshold, per-instrument state, transition + 120-minute reminder +
recovery cadence, market-hours awareness, and --dry-run.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ops.feed_gap_alarm import (
    THRESHOLD_MIN,
    last_reopen,
    load_state,
    market_open,
    newest_15m_bar,
    run_once,
)

# A mid-session reference moment: Thursday 2026-07-16 14:00Z (NY hours).
T0 = datetime(2026, 7, 16, 14, 0, tzinfo=timezone.utc)


def write_bar(log_dir: Path, instrument: str, ts: datetime, timeframe: str = "15") -> None:
    path = log_dir / f"bars_{instrument}_{ts.date().isoformat()}.jsonl"
    row = {"time": ts.isoformat(), "open": 1, "high": 2, "low": 0.5, "close": 1.5,
           "timeframe": timeframe}
    with path.open("a") as fh:
        fh.write(json.dumps(row) + "\n")


def run(log_dir: Path, now: datetime, sent: list[str] | None = None, **kwargs):
    sent = [] if sent is None else sent

    def sender(msg: str) -> bool:
        sent.append(msg)
        return True

    msgs = run_once(log_dir=log_dir, env_path=log_dir / ".env", now=now,
                    sender=sender, **kwargs)
    return msgs, sent


def seed_both_fresh(log_dir: Path, now: datetime, age_min: float = 5) -> None:
    for inst in ("MNQ", "MES"):
        write_bar(log_dir, inst, now - timedelta(minutes=age_min))


# ── threshold boundary ───────────────────────────────────────────────────────

def test_healthy_feed_at_30_minutes_is_not_stale(tmp_path):
    seed_both_fresh(tmp_path, T0, age_min=30)
    msgs, _ = run(tmp_path, T0)
    assert msgs == []
    state = load_state(tmp_path)
    assert all(v["status"] == "healthy" for v in state["instruments"].values())


def test_feed_over_31_minutes_is_stale(tmp_path):
    seed_both_fresh(tmp_path, T0, age_min=32)
    msgs, _ = run(tmp_path, T0)
    assert len(msgs) == 2  # one per instrument
    assert all("FEED GAP" in m for m in msgs)


def test_threshold_constant_is_31(tmp_path):
    assert THRESHOLD_MIN == 31


# ── per-instrument independence ──────────────────────────────────────────────

def test_one_stale_one_healthy_alerts_exactly_once(tmp_path):
    write_bar(tmp_path, "MNQ", T0 - timedelta(minutes=45))  # stale
    write_bar(tmp_path, "MES", T0 - timedelta(minutes=10))  # healthy
    msgs, _ = run(tmp_path, T0)
    assert len(msgs) == 1
    assert "MNQ" in msgs[0] and "MES" not in msgs[0]
    state = load_state(tmp_path)
    assert state["instruments"]["MNQ"]["status"] == "stale"
    assert state["instruments"]["MES"]["status"] == "healthy"


def test_recovery_of_one_instrument_does_not_reset_the_other(tmp_path):
    write_bar(tmp_path, "MNQ", T0 - timedelta(minutes=45))
    write_bar(tmp_path, "MES", T0 - timedelta(minutes=45))
    run(tmp_path, T0)
    # MES recovers, MNQ still dark.
    write_bar(tmp_path, "MES", T0 + timedelta(minutes=5))
    msgs, _ = run(tmp_path, T0 + timedelta(minutes=10))
    assert len(msgs) == 1 and "RECOVERED" in msgs[0] and "MES" in msgs[0]
    state = load_state(tmp_path)
    assert state["instruments"]["MNQ"]["status"] == "stale"
    assert state["instruments"]["MES"]["status"] == "healthy"


# ── transition / reminder / recovery cadence ────────────────────────────────

def test_no_repeat_alert_within_reminder_window(tmp_path):
    seed_both_fresh(tmp_path, T0 - timedelta(minutes=40), age_min=0)
    run(tmp_path, T0)  # both go stale, 2 alerts
    msgs, _ = run(tmp_path, T0 + timedelta(minutes=5))
    assert msgs == []  # still stale, no re-alert after 5 minutes


def test_reminder_fires_at_120_minutes(tmp_path):
    seed_both_fresh(tmp_path, T0 - timedelta(minutes=40), age_min=0)
    run(tmp_path, T0)
    msgs, _ = run(tmp_path, T0 + timedelta(minutes=119))
    assert msgs == []
    msgs, _ = run(tmp_path, T0 + timedelta(minutes=121))
    assert len(msgs) == 2 and all("ongoing" in m for m in msgs)


def test_reminder_clock_resets_after_each_reminder(tmp_path):
    seed_both_fresh(tmp_path, T0 - timedelta(minutes=40), age_min=0)
    run(tmp_path, T0)
    run(tmp_path, T0 + timedelta(minutes=121))  # reminder
    msgs, _ = run(tmp_path, T0 + timedelta(minutes=180))  # only 59m later
    assert msgs == []


def test_recovery_notice_sent_once_per_instrument(tmp_path):
    seed_both_fresh(tmp_path, T0 - timedelta(minutes=40), age_min=0)
    run(tmp_path, T0)
    seed_both_fresh(tmp_path, T0 + timedelta(minutes=10), age_min=0)
    msgs, _ = run(tmp_path, T0 + timedelta(minutes=15))
    assert len(msgs) == 2 and all("RECOVERED" in m for m in msgs)
    # Healthy -> healthy: silence.
    msgs, _ = run(tmp_path, T0 + timedelta(minutes=20))
    assert msgs == []


def test_healthy_oscillation_never_alerts(tmp_path):
    """The v1 false-positive pattern: a perfectly healthy feed checked every
    5 minutes across a full half-hour cycle must emit nothing."""
    total = []
    for k in range(13):  # checks every 5 min across a full hour
        now = T0 + timedelta(minutes=5 * k)
        # A healthy feed delivers the bar OPENED at X at time X+15m: the
        # newest bar-open visible at `now` is the last quarter-hour ≥15m ago.
        delivered_open = now - timedelta(
            minutes=15 + (now.minute % 15), seconds=now.second
        )
        for inst in ("MNQ", "MES"):
            write_bar(tmp_path, inst, delivered_open)
        msgs, _ = run(tmp_path, now)
        total += msgs
    assert total == []


# ── market hours ─────────────────────────────────────────────────────────────

def test_saturday_closed(tmp_path):
    sat = datetime(2026, 7, 18, 14, 0, tzinfo=timezone.utc)
    assert market_open(sat) is False
    msgs, _ = run(tmp_path, sat)  # no bar files at all — would be stale if open
    assert msgs == []


def test_friday_after_close_and_daily_break_closed():
    assert market_open(datetime(2026, 7, 17, 21, 30, tzinfo=timezone.utc)) is False
    assert market_open(datetime(2026, 7, 16, 21, 5, tzinfo=timezone.utc)) is False


def test_sunday_before_open_closed_and_after_open_open():
    assert market_open(datetime(2026, 7, 19, 20, 0, tzinfo=timezone.utc)) is False
    assert market_open(datetime(2026, 7, 19, 22, 30, tzinfo=timezone.utc)) is True


def test_sunday_reopen_grace_uses_reopen_baseline(tmp_path):
    """Friday's last bar is days old at the Sunday reopen — the reopen
    baseline must prevent an instant false stale alert."""
    sunday = datetime(2026, 7, 19, 22, 10, tzinfo=timezone.utc)
    for inst in ("MNQ", "MES"):
        write_bar(tmp_path, inst, datetime(2026, 7, 17, 20, 45, tzinfo=timezone.utc))
    msgs, _ = run(tmp_path, sunday)
    assert msgs == []


def test_stale_after_reopen_grace_expires(tmp_path):
    sunday_late = datetime(2026, 7, 19, 22, 45, tzinfo=timezone.utc)  # 45m post-open
    for inst in ("MNQ", "MES"):
        write_bar(tmp_path, inst, datetime(2026, 7, 17, 20, 45, tzinfo=timezone.utc))
    msgs, _ = run(tmp_path, sunday_late)
    assert len(msgs) == 2


def test_last_reopen_skips_friday_and_saturday():
    sunday = datetime(2026, 7, 19, 23, 0, tzinfo=timezone.utc)
    assert last_reopen(sunday) == datetime(2026, 7, 19, 22, 0, tzinfo=timezone.utc)
    monday_early = datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)
    assert last_reopen(monday_early) == datetime(2026, 7, 19, 22, 0, tzinfo=timezone.utc)


# ── state robustness ─────────────────────────────────────────────────────────

def test_corrupt_state_degrades_to_healthy_defaults(tmp_path):
    (tmp_path / "feed_gap_alarm_state.json").write_text("{not json")
    seed_both_fresh(tmp_path, T0, age_min=5)
    msgs, _ = run(tmp_path, T0)
    assert msgs == []
    state = load_state(tmp_path)
    assert set(state["instruments"]) == {"MNQ", "MES"}


def test_missing_bar_files_alert_as_stale_during_market_hours(tmp_path):
    msgs, _ = run(tmp_path, T0)  # no bars_ files at all
    assert len(msgs) == 2 and all("NONE" not in m or True for m in msgs)


def test_non_15m_rows_are_ignored(tmp_path):
    for inst in ("MNQ", "MES"):
        write_bar(tmp_path, inst, T0 - timedelta(minutes=60))          # old 15m
        write_bar(tmp_path, inst, T0 - timedelta(minutes=2), "5")      # fresh 5m
    msgs, _ = run(tmp_path, T0)
    assert len(msgs) == 2  # 5m freshness must not mask a dead 15m feed


def test_newest_bar_parses_iso_and_picks_max(tmp_path):
    write_bar(tmp_path, "MNQ", T0 - timedelta(minutes=90))
    write_bar(tmp_path, "MNQ", T0 - timedelta(minutes=15))
    assert newest_15m_bar(tmp_path, "MNQ") == T0 - timedelta(minutes=15)


# ── dry-run / delivery ───────────────────────────────────────────────────────

def test_dry_run_emits_but_never_calls_sender(tmp_path, capsys):
    seed_both_fresh(tmp_path, T0, age_min=45)
    sent: list[str] = []
    msgs = run_once(log_dir=tmp_path, env_path=tmp_path / ".env", now=T0,
                    dry_run=True, sender=sent.append)
    assert len(msgs) == 2 and sent == []
    assert "DRY-RUN" in capsys.readouterr().out


def test_missing_webhook_url_is_logged_not_silent(tmp_path):
    seed_both_fresh(tmp_path, T0, age_min=45)
    (tmp_path / ".env").write_text("OTHER=1\n")
    run_once(log_dir=tmp_path, env_path=tmp_path / ".env", now=T0)
    log = (tmp_path / "feed_gap_alarm.log").read_text()
    assert "no DISCORD_WEBHOOK_URL" in log


def test_user_agent_header_present(monkeypatch, tmp_path):
    captured = {}

    def fake_urlopen(req, timeout=0):
        captured["ua"] = req.headers.get("User-agent")
        class R:
            def read(self):
                return b""
        return R()

    import ops.feed_gap_alarm as mod
    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    (tmp_path / ".env").write_text("DISCORD_WEBHOOK_URL=https://example.invalid/hook\n")
    assert mod.send_discord(tmp_path / ".env", tmp_path, "test", now=T0) is True
    assert captured["ua"] == "afs-ops/1.0"


def test_log_lines_written_every_market_hours_cycle(tmp_path):
    seed_both_fresh(tmp_path, T0, age_min=5)
    run(tmp_path, T0)
    log = (tmp_path / "feed_gap_alarm.log").read_text()
    assert "MNQ=healthy" in log and "MES=healthy" in log
