"""ACTION REQUIRED cards — the watcher's Discord text for findings that need a human now.

Scenario is the real 2026-09-14 outage: TradingView alerts were re-created with an
empty webhook secret, every POST went 401 from 16:45Z, the feed-gap alarm raised
feed_MES_stale / feed_MNQ_stale, and the Daily 2-2 paper SHORT sat un-evaluable for
5h20m while the posts read like routine telemetry.  These tests pin the card shape
and — more importantly — that the layer never changes which findings exist.
"""
import importlib.util
import sys
from datetime import timedelta
from pathlib import Path

import pytest


WATCHER_DIR = Path(__file__).parent.parent / "ops" / "afs_watcher"


def _load_watcher():
    if str(WATCHER_DIR) not in sys.path:
        sys.path.insert(0, str(WATCHER_DIR))
    spec = importlib.util.spec_from_file_location("afs_watcher_action_cards", WATCHER_DIR / "watcher.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


w = _load_watcher()

D22_POS = {"strategy": "DAILY_22_CONTINUATION_FIRST_BREAK", "direction": "SHORT", "entry": 29338.25,
           "stop": 29634.25, "target": 28728.25, "entry_time": "2026-09-10T11:10:00+00:00",
           "paper_order_id": "PAPER-38b351ef"}


def _tick(open_lanes=("daily_22_5k",), *, mnq_age_min=35, mes_age_min=35):
    now = w.now_utc()
    inv = {
        "daily_22_5k": {"open_position": D22_POS if "daily_22_5k" in open_lanes else None},
        "mes_122_1500": {"open_position": {"direction": "LONG", "entry": 6500.0} if "mes_122_1500" in open_lanes else None},
    }
    return {
        "verdict": "BLOCKED",
        "lanes": {
            "open_positions": list(open_lanes),
            "inventory": inv,
            "newest_5m_mnq_bar_mtime": w.iso(now - timedelta(minutes=mnq_age_min)),
            "newest_15m_mnq_bar_mtime": w.iso(now - timedelta(minutes=mnq_age_min)),
            "newest_mes_15m_bar_mtime": w.iso(now - timedelta(minutes=mes_age_min)),
        },
    }


FEED_MNQ = {"level": "BLOCKED", "key": "feed_MNQ_stale",
            "summary": "feed-gap alarm: MNQ status=stale stale_since=2026-09-14T17:05:01+00:00", "detail": {}}


# ── classification: only the whitelisted conditions, only with exposure ──────
def test_feed_stale_is_action_required_only_when_the_matching_lane_is_open():
    assert w.action_required("feed_MNQ_stale", _tick(("daily_22_5k",))) is True
    assert w.action_required("feed_MNQ_stale", _tick(())) is False
    # MES feed stale does not expose an MNQ-only lane; it does expose the MES lane
    assert w.action_required("feed_MES_stale", _tick(("daily_22_5k",))) is False
    assert w.action_required("feed_MES_stale", _tick(("mes_122_1500",))) is True


def test_bot_process_and_broker_conditions_are_always_action_required():
    for key in ("service_not_active", "unexpected_restart", "unexpected_broker_position",
                "tradovate_auth_failed", "post_epoch_wrong_sha", "daily_22_halted"):
        assert w.action_required(key, _tick(())) is True


def test_routine_conditions_never_become_cards():
    for key in ("post_epoch_spans_releases", "memory_rss_growth", "swap_used_warning", "orb_reclaim_unpaired"):
        assert w.action_required(key, _tick(("daily_22_5k",))) is False


# ── card shape ───────────────────────────────────────────────────────────────
def test_action_card_matches_the_operator_layout(monkeypatch):
    monkeypatch.setattr(w, "RELEASE_SHA", "28f79a65a395")
    first = "2026-09-14T17:05:01+00:00"
    text = w._action_card_text("feed_MNQ_stale", FEED_MNQ, _tick(("daily_22_5k",), mnq_age_min=35), first)
    lines = text.splitlines()
    assert lines[0] == "🛑 **ACTION REQUIRED — MNQ FEED STALE**"
    assert lines[1] == "**Exposure:** Daily 2-2 paper SHORT is OPEN @ 29338.25"
    assert lines[2] == "**Problem:** No MNQ bars for 35 min"
    assert lines[3] == "**Impact:** Stop/target cannot be evaluated while feed is down"
    assert lines[4] == "**Do now:** Check TradingView alerts / webhook delivery"
    assert lines[5] == "**Since:** 1:05 PM ET"
    assert lines[6] == "`feed_MNQ_stale · read-only`"
    assert len(lines) == 7
    # the card never carries the paragraph-style telemetry of the plain text
    assert "What to check" not in text and "Snapshot:" not in text


def test_resolved_card_reports_duration_and_remaining_exposure():
    first = w.iso(w.now_utc() - timedelta(minutes=295))
    text = w._resolved_card_text("feed_MNQ_stale", first, _tick(("daily_22_5k",), mnq_age_min=0))
    assert text.splitlines() == [
        "✅ **RESOLVED — MNQ FEED RECOVERED**",
        "Bars flowing again after 295 min.",
        "Daily 2-2 paper position remains OPEN (SHORT @ 29338.25).",
        "`feed_MNQ_stale · read-only`",
    ]


# ── handle_blocked wiring: findings untouched, text swapped, resolve matches raise ──
def _run(monkeypatch, state, findings, tick):
    notifications = []
    monkeypatch.setattr(w, "capture_snapshot", lambda *_a: Path("/tmp/afs_watcher/snapshots/x"))
    monkeypatch.setattr(w, "state_append", lambda *_a: None)
    monkeypatch.setattr(w, "log", lambda *_a: None)
    monkeypatch.setattr(w, "notify", lambda _s, route, text, _d: notifications.append((route, text)))
    monkeypatch.setattr(w, "RELEASE_SHA", "28f79a65a395")
    w.handle_blocked(state, findings, tick)
    return notifications


def test_feed_stale_with_open_lane_raises_a_card_and_resolves_as_a_card(monkeypatch):
    state = {"blocked": {}, "blocked_last_notified": {}, "notified": {}}
    findings = w.Findings()
    findings.add("BLOCKED", "feed_MNQ_stale", FEED_MNQ["summary"])

    raised = _run(monkeypatch, state, findings, _tick(("daily_22_5k",)))
    assert raised[0][0] == "DISCORD_ROUTE_ERROR"
    assert raised[0][1].startswith("🛑 **ACTION REQUIRED — MNQ FEED STALE**")
    assert state["blocked"]["feed_MNQ_stale"]["action_required"] is True
    # the finding itself is untouched: same key, same summary, still BLOCKED
    assert findings.blocked()[0]["summary"] == FEED_MNQ["summary"]

    cleared = _run(monkeypatch, state, w.Findings(), _tick(("daily_22_5k",), mnq_age_min=0))
    assert cleared[0][1].startswith("✅ **RESOLVED — MNQ FEED RECOVERED**")
    assert "Daily 2-2 paper position remains OPEN" in cleared[0][1]
    assert state["blocked"] == {}


def test_feed_stale_without_exposure_keeps_the_plain_blocked_text(monkeypatch):
    state = {"blocked": {}, "blocked_last_notified": {}, "notified": {}}
    findings = w.Findings()
    findings.add("BLOCKED", "feed_MNQ_stale", FEED_MNQ["summary"])

    raised = _run(monkeypatch, state, findings, _tick(()))
    assert raised[0][1].startswith("🛑 **BLOCKED — Feed mnq stale**")
    assert state["blocked"]["feed_MNQ_stale"]["action_required"] is False

    cleared = _run(monkeypatch, state, w.Findings(), _tick(()))
    assert cleared[0][1].startswith("✅ **cleared — Feed mnq stale**")


def test_exposure_appearing_later_promotes_to_a_card_once(monkeypatch):
    state = {"blocked": {}, "blocked_last_notified": {}, "notified": {}}
    findings = w.Findings()
    findings.add("BLOCKED", "feed_MNQ_stale", FEED_MNQ["summary"])

    first = _run(monkeypatch, state, findings, _tick(()))
    assert first[0][1].startswith("🛑 **BLOCKED —")
    # lane opens while the feed is still stale: re-notify immediately as a card
    second = _run(monkeypatch, state, findings, _tick(("daily_22_5k",)))
    assert len(second) == 1 and second[0][1].startswith("🛑 **ACTION REQUIRED —")
    # and only once — the reminder cadence takes over from here
    third = _run(monkeypatch, state, findings, _tick(("daily_22_5k",)))
    assert third == []


@pytest.mark.parametrize("key", ["watcher_release_stale", "post_epoch_spans_releases"])
def test_non_card_conditions_are_byte_identical_to_before(monkeypatch, key):
    """Regression guard: the existing plain text path is unchanged for everything else."""
    state = {"blocked": {}, "blocked_last_notified": {}, "notified": {}}
    findings = w.Findings()
    findings.add("BLOCKED", key, "summary")
    raised = _run(monkeypatch, state, findings, _tick(("daily_22_5k",)))
    assert raised == [("DISCORD_ROUTE_ERROR", w._blocked_discord_text(key, findings.blocked()[0], "/tmp/afs_watcher/snapshots/x"))]
