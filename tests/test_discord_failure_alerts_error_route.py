"""Failure / safety alerts go to the Discord ``error`` route, not heartbeat.

Policy (2026-09-16): heartbeat = "system is alive/status", error = "something
is wrong / a human should notice", observation = "research campaign event",
signal = "paper strategy decision". This file proves the feed-watchdog stale /
authoritative-15m / transport alerts and their recoveries, plus the runner's
EXECUTION SAFETY and LIVE ORDER BLOCKED alerts, use the ``error`` route, fall
back to the legacy webhook only when that route is unset, stay fail-soft, and
never touch observation, signal, or heartbeat.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from context.bar_history import BarHistory
from execution import cross_instrument_observation as cio
from notifications.discord_notifier import NotificationResult, send_operational_alert
from notifications.discord_router import DiscordRouter
from scripts import feed_watchdog as fw

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 14, 14, 0, tzinfo=timezone.utc)  # Monday 10:00 ET
EPOCH = "error-route-epoch"


@pytest.fixture
def capture_router(monkeypatch):
    sent: list[tuple[str, str]] = []

    def transport(url, message):
        sent.append((url, message))

    real_init = DiscordRouter.__init__

    def patched_init(self, routes=None, routes_path=None, transport_=None, env=None):
        real_init(self, routes=routes, routes_path=routes_path, transport=transport, env=env)

    monkeypatch.setattr(DiscordRouter, "__init__", patched_init)
    return sent


@pytest.fixture
def routes(monkeypatch):
    monkeypatch.setenv("DISCORD_ROUTE_ERROR", "https://error.invalid/route")
    monkeypatch.setenv("DISCORD_ROUTE_HEARTBEAT", "https://heartbeat.invalid/route")
    monkeypatch.setenv("DISCORD_ROUTE_SIGNAL", "https://signal.invalid/route")
    monkeypatch.setenv("DISCORD_ROUTE_OBSERVATION", "https://obs.invalid/route")


def _cfg(tmp_path):
    return SimpleNamespace(log_dir=str(tmp_path), expected_timeframe_minutes=15,
                           discord_webhook_url="https://legacy-heartbeat.invalid/hook",
                           discord_notifications_enabled=True)


def _write_latest(tmp_path, root, received_at, tf="15"):
    name = "latest_webhook.json" if root is None else f"latest_webhook_{root}.json"
    (tmp_path / name).write_text(json.dumps({"received_at": received_at.isoformat(),
                                             "payload": {"timeframe": tf, "ticker": f"{root}1!"}}), encoding="utf-8")


# ── helper semantics ──────────────────────────────────────────────────────────

def test_operational_alert_uses_error_route_when_set(routes, capture_router, tmp_path):
    res = send_operational_alert(_cfg(tmp_path), "🚨 something is wrong")
    assert res.sent is True and res.reason == "sent"
    assert capture_router == [("https://error.invalid/route", "🚨 something is wrong")]


def test_operational_alert_falls_back_to_legacy_only_when_error_route_unset(monkeypatch, capture_router, tmp_path):
    monkeypatch.delenv("DISCORD_ROUTE_ERROR", raising=False)
    monkeypatch.setenv("DISCORD_ROUTE_HEARTBEAT", "https://heartbeat.invalid/route")
    legacy: list[tuple[str, bytes]] = []
    monkeypatch.setattr("notifications.discord_notifier._post_json", lambda url, body, headers: legacy.append((url, body)))
    res = send_operational_alert(_cfg(tmp_path), "fallback alert")
    assert res.sent is True
    assert capture_router == []                                   # router never used heartbeat
    assert legacy and legacy[0][0] == "https://legacy-heartbeat.invalid/hook"


def test_operational_alert_is_fail_soft(routes, monkeypatch, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("discord down")
    monkeypatch.setattr("notifications.discord_router._default_transport", boom)
    res = send_operational_alert(_cfg(tmp_path), "x")
    assert isinstance(res, NotificationResult) and res.sent is False


# ── feed-watchdog: stale / recovery → error, paired ──────────────────────────

def test_watchdog_legacy_stale_and_recovery_both_go_to_error_route(routes, capture_router, tmp_path):
    cfg = _cfg(tmp_path)
    _write_latest(tmp_path, None, NOW - timedelta(minutes=2))
    _write_latest(tmp_path, "MNQ", NOW - timedelta(minutes=2))
    _write_latest(tmp_path, "M2K", NOW - timedelta(minutes=90))
    out = fw.run(now=NOW, config=cfg)                            # default sender = error route
    assert out["instruments"]["stale"] and "M2K" in out["instruments"]["stale"][0]
    _write_latest(tmp_path, "M2K", NOW + timedelta(minutes=4))
    out2 = fw.run(now=NOW + timedelta(minutes=5), config=cfg)
    assert out2["instruments"]["recovered"] == ["M2K"]
    urls = [u for u, _ in capture_router]
    assert urls and set(urls) == {"https://error.invalid/route"}
    msgs = [m for _, m in capture_router]
    assert any("No price updates for some markets" in m and "M2K (Micro Russell)" in m for m in msgs)
    assert any("Price updates are back: M2K (Micro Russell)" in m for m in msgs)


def test_watchdog_authoritative_15m_stale_and_transport_error_go_to_error_route(routes, capture_router, tmp_path, monkeypatch):
    monkeypatch.setenv(cio.ENV_NAME, cio.CAMPAIGN_ID)
    monkeypatch.setenv(cio.EPOCH_ENV_NAME, EPOCH)
    stale_ts = (NOW - timedelta(minutes=90)).isoformat()
    state = {"campaign_id": cio.CAMPAIGN_ID, "pending": {}, "seen_candidate_ids": [],
             "seen_bars": [f"{EPOCH}|M2K|15|{stale_ts}", f"{EPOCH}|MGC|15|{(NOW - timedelta(minutes=15)).isoformat()}"],
             "strat_212_122": {}}
    (tmp_path / cio.STATE_FILENAME).write_text(json.dumps(state), encoding="utf-8")
    for root, ts in (("M2K", stale_ts), ("MGC", (NOW - timedelta(minutes=15)).isoformat())):
        BarHistory(log_dir=str(tmp_path)).record(root, ts=ts, open=1, high=2, low=0, close=1, volume=1, timeframe="15")
    (tmp_path / f"{cio.CAMPAIGN_ID}_transport_MGC.json").write_text(json.dumps({
        "campaign_id": cio.CAMPAIGN_ID, "evidence_epoch": EPOCH, "attempted_at": NOW.isoformat(),
        "bar_ts": NOW.isoformat(), "timeframe_minutes": 15, "transport_ok": False, "bar_recorded": True,
        "last_error": "synthetic detector failure"}), encoding="utf-8")
    _write_latest(tmp_path, None, NOW - timedelta(minutes=1))
    out = fw.run(now=NOW, config=_cfg(tmp_path))
    assert out["instruments"]["authority"] != "webhook_receipt_legacy"
    assert set(u for u, _ in capture_router) == {"https://error.invalid/route"}
    msgs = [m for _, m in capture_router]
    assert any("missing 15-minute price bars" in m and "M2K (Micro Russell)" in m for m in msgs)
    assert any("MGC (Micro Gold): bars arrive but couldn't be processed" in m for m in msgs)
    # Raw error text stays out of the body: it rides in the small footer line.
    assert any("MGC: synthetic detector failure" in m for m in msgs)


def test_watchdog_healthy_tick_sends_nothing_and_never_uses_heartbeat_or_observation(routes, capture_router, tmp_path):
    _write_latest(tmp_path, None, NOW - timedelta(minutes=1))
    _write_latest(tmp_path, "MNQ", NOW - timedelta(minutes=1))
    out = fw.run(now=NOW, config=_cfg(tmp_path))
    assert out["action"] == "ok" and capture_router == []
    src = (ROOT / "scripts" / "feed_watchdog.py").read_text(encoding="utf-8")
    assert "send_discord_alert" not in src and '"heartbeat"' not in src and "observation_notifier" not in src


def test_watchdog_unset_error_route_falls_back_and_never_crashes(monkeypatch, capture_router, tmp_path):
    monkeypatch.delenv("DISCORD_ROUTE_ERROR", raising=False)
    legacy: list[str] = []
    monkeypatch.setattr("notifications.discord_notifier._post_json", lambda url, body, headers: legacy.append(url))
    _write_latest(tmp_path, None, NOW - timedelta(minutes=2))
    _write_latest(tmp_path, "MES", NOW - timedelta(minutes=90))
    out = fw.run(now=NOW, config=_cfg(tmp_path))
    assert out["instruments"]["stale"]
    assert capture_router == [] and legacy == ["https://legacy-heartbeat.invalid/hook"]


# ── runner: EXECUTION SAFETY / LIVE ORDER BLOCKED → error ────────────────────

def test_runner_tradovate_safety_alerts_use_operational_error_helper():
    src = (ROOT / "webhook" / "runner.py").read_text(encoding="utf-8")
    for needle in ("EXECUTION SAFETY: Tradovate order did not remain open.",
                   "LIVE ORDER BLOCKED: broker reported OPEN but returned no order ids."):
        idx = src.index(needle)
        window = src[max(0, idx - 400): idx]
        assert "send_operational_alert(" in window, needle
        assert "send_discord_alert(" not in window, needle
    # The only remaining legacy alert call in the runner is the daily_report fallback.
    assert src.count("send_discord_alert(") == 1


def test_error_route_change_does_not_touch_signal_observation_or_heartbeat_routes(routes, capture_router, tmp_path):
    router = DiscordRouter()
    assert router.send("signal", "MNQ TRADE") and router.send("observation", "MGC — OBSERVATION ONLY — x") \
        and router.send("heartbeat", "alive")
    assert [u for u, _ in capture_router] == ["https://signal.invalid/route", "https://obs.invalid/route",
                                              "https://heartbeat.invalid/route"]
    for path in ("config/notification_routes.yaml", "notifications/discord_notifier.py", "scripts/feed_watchdog.py"):
        assert "discord.com/api/webhooks" not in (ROOT / path).read_text(encoding="utf-8")
