from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from notifications.heartbeat import build_heartbeat_message, maybe_send_heartbeat


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
