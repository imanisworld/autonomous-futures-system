from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from notifications import heartbeat as hb
from notifications.heartbeat import (
    build_heartbeat_message,
    maybe_send_heartbeat,
    run_heartbeat_loop,
)


def _config(enabled: bool = True):
    return SimpleNamespace(
        discord_heartbeat_enabled=enabled,
        discord_webhook_url="https://discord.test/webhook",
    )


def _write_latest_webhook(log_dir, *, age_minutes: float):
    received = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    (log_dir / "latest_webhook.json").write_text(json.dumps({
        "received_at": received.isoformat(),
        "payload": {"ticker": "MES1!", "close": 5000.0},
    }))


def test_build_heartbeat_message_formats_summary():
    msg = build_heartbeat_message(
        session="asian", last_bar_age_s=300, has_open_position=False,
        trades_today=2, pnl_today=55.0,
    )
    assert "asian session" in msg
    assert "last bar 5m ago" in msg
    assert "flat" in msg
    assert "2 trade(s) today" in msg
    assert "$55.00" in msg


def test_heartbeat_disabled_is_noop(tmp_path):
    sent = []
    result = maybe_send_heartbeat(_config(enabled=False), str(tmp_path),
                                  sender=lambda *a, **k: sent.append(a))
    assert result is None
    assert sent == []


def test_heartbeat_sends_when_bars_are_fresh(tmp_path):
    _write_latest_webhook(tmp_path, age_minutes=4)
    captured = {}

    def fake_sender(url, body, headers):
        captured["body"] = json.loads(body.decode("utf-8"))

    result = maybe_send_heartbeat(_config(), str(tmp_path), sender=fake_sender)
    assert result is not None
    assert "heartbeat" in result
    assert captured["body"]["content"] == result


def test_heartbeat_skips_when_market_quiet(tmp_path):
    # Last bar 2h old → market closed / feed down → no ping (watchdog owns that).
    _write_latest_webhook(tmp_path, age_minutes=120)
    sent = []
    result = maybe_send_heartbeat(_config(), str(tmp_path),
                                  sender=lambda *a, **k: sent.append(a))
    assert result is None
    assert sent == []


def test_heartbeat_skips_when_no_webhook_file(tmp_path):
    sent = []
    result = maybe_send_heartbeat(_config(), str(tmp_path),
                                  sender=lambda *a, **k: sent.append(a))
    assert result is None
    assert sent == []


def test_heartbeat_never_raises_on_bad_state(tmp_path):
    # Corrupt latest_webhook.json → must be swallowed, return None.
    (tmp_path / "latest_webhook.json").write_text("{not json")
    assert maybe_send_heartbeat(_config(), str(tmp_path)) is None


def test_loop_pings_at_startup_then_hourly(tmp_path, monkeypatch):
    # The loop must ping shortly after startup (short grace delay), NOT after a
    # full hour — so a restart promptly re-confirms liveness instead of going
    # dark. Under the old "sleep a full interval first" behaviour sleeps[0] would
    # be the interval and no ping would precede it.
    pings = []
    monkeypatch.setattr(hb, "maybe_send_heartbeat", lambda *a, **k: pings.append(len(sleeps)))

    sleeps: list[float] = []

    class _Stop(Exception):
        pass

    async def fake_sleep(secs):
        sleeps.append(secs)
        if len(sleeps) >= 3:  # startup delay, one interval, then break out
            raise _Stop()

    with pytest.raises(_Stop):
        asyncio.run(run_heartbeat_loop(
            _config(), str(tmp_path),
            interval_seconds=3600, startup_delay_seconds=60, sleep=fake_sleep,
        ))

    assert sleeps[0] == 60       # startup grace, not the full hour
    assert sleeps[1] == 3600     # then hourly cadence
    # First ping landed after the 60s grace (1 sleep done), before any 3600 sleep.
    assert pings[0] == 1
    assert len(pings) == 2       # startup ping + one hourly ping
