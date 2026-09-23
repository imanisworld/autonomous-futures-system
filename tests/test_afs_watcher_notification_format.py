import importlib.util
import json
import sys
from pathlib import Path


WATCHER_DIR = Path(__file__).parent.parent / "ops" / "afs_watcher"


def _load_watcher():
    if str(WATCHER_DIR) not in sys.path:
        sys.path.insert(0, str(WATCHER_DIR))
    spec = importlib.util.spec_from_file_location(
        "afs_watcher_notification_format", WATCHER_DIR / "watcher.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


w = _load_watcher()


def test_known_blocked_messages_lead_with_a_plain_english_headline(monkeypatch):
    monkeypatch.setattr(w, "RELEASE_SHA", "ee2d0b1d7292cae1b6195f8e2045308a818272c3")

    stale = w._blocked_discord_text("watcher_release_stale")
    restart = w._blocked_discord_text("unexpected_restart")

    assert stale.splitlines()[0] == "🛑 Watcher is still checking the old version"
    assert "What's wrong: A new version of the bot is live, but the watcher is still checking against the previous one." in stale
    assert "What to do: Restart the watcher only — the bot itself is fine" in stale
    assert stale.splitlines()[-1] == "-# watcher_release_stale · version ee2d0b1d"

    assert restart.splitlines()[0] == "🛑 Trading bot restarted without a planned update"
    assert "What's wrong: The bot restarted and no planned update explains it." in restart
    assert restart.splitlines()[-1] == "-# unexpected_restart · version ee2d0b1d"


def test_blocked_message_surfaces_the_first_offending_line_and_snapshot(monkeypatch):
    monkeypatch.setattr(w, "RELEASE_SHA", "5115b780f2b8")
    sample = '2026-09-11T03:44:23+00:00 futures-bot python[1]: INFO: 127.0.0.1:0 - "POST /webhook/alert HTTP/1.0" 422 Unprocessable Content'
    finding = {
        "summary": "1 non-200 alert responses since 2026-09-11T03:41:15Z",
        "detail": {"counts": {"200": 4, "422": 1}, "samples": [sample]},
    }

    text = w._blocked_discord_text("alert_non200", finding, "/tmp/afs_watcher/snapshots/x")
    lines = text.splitlines()

    assert lines[0] == "🛑 Bot is rejecting TradingView alerts"
    assert lines[1] == "What's wrong: TradingView is sending alerts but the bot is turning some of them away."
    assert lines[2].startswith("What to do: Look at the rejected alerts")
    assert lines[3] == "-# snapshot x"
    assert lines[4] == "-# alert_non200 · version 5115b780"
    # the raw offending line is still there for debugging, but only in the small footer
    assert lines[5] == f"-# log: {sample[-110:]}"
    assert "non-200" not in "\n".join(lines[:3])


def test_unknown_blocked_key_has_safe_readable_fallback(monkeypatch):
    monkeypatch.setattr(w, "RELEASE_SHA", "0123456789abcdef")

    text = w._blocked_discord_text("new_unknown_finding")

    assert text.splitlines()[0] == "🛑 New unknown finding"
    assert f"What to do: {w._DEFAULT_FIX}" in text
    assert "-# new_unknown_finding · version 01234567" in text


def test_warning_uses_the_same_layout_with_a_warning_icon(monkeypatch):
    monkeypatch.setattr(w, "RELEASE_SHA", "0123456789abcdef")
    text = w._finding_discord_text("WARNING", "post_epoch_spans_releases", {"summary": "spans 4 releases", "detail": {}}, "/snap")
    assert text.splitlines()[0] == "⚠️ Test records come from several versions"
    assert "-# detail: spans 4 releases" in text
    assert "-# snapshot snap" in text


def test_daily_pass_is_multiline_and_readable(monkeypatch):
    monkeypatch.setattr(w, "now_utc", lambda: w._ts("2026-09-10T21:00:00Z"))
    pops = {
        f"{strategy}/{variant}": {"candidates": 0, "resolved_filled_economic": 0, "distinct_trading_days": 0}
        for strategy, variant in w.EXPECTED_POPULATIONS
    }
    pops["vwap_hold/control"] = {"candidates": 48, "resolved_filled_economic": 39, "distinct_trading_days": 18}
    text = w._daily_discord_text("DAILY PASS", "2026-09-10", {"200": 548}, 22, pops, [], "/tmp/afs_watcher/daily/2026-09-10.json")
    assert text.splitlines()[0] == "✅ Daily check: all good (Thu Sep 10)"
    assert "**Alerts received:** 548, all accepted" in text
    assert "**Forward test:** 5 of 5 versions reporting, 22 records so far" in text
    quiet = next(l for l in text.splitlines() if l.startswith("**No setups yet:**"))
    assert "Back inside the opening range (original rules)" in quiet
    assert "Holding the day's average price (original rules)" not in quiet
    assert "**What to do:** nothing" in text
    assert "-# report /tmp/afs_watcher/daily/2026-09-10.json" in text


def test_daily_blocked_lists_discrepancies_and_rejected_posts():
    text = w._daily_discord_text("DAILY BLOCKED", "2026-09-10", {"200": 5, "422": 1}, 0, {}, ["open BLOCKED conditions: ['alert_non200']"], "/f")
    assert text.splitlines()[0] == "🛑 Daily check: problems found (Thu Sep 10)"
    assert "**Alerts received:** 6, 1 turned away" in text
    assert "⚠️ Still not fixed: Bot is rejecting TradingView alerts" in text
    # no Python dict/list reprs reach Discord
    assert "{" not in text and "[" not in text


def test_notification_presentation_does_not_change_event_or_state_semantics(monkeypatch, tmp_path):
    snapshot = tmp_path / "snapshot"
    state = {
        "blocked": {},
        "blocked_last_notified": {},
        "notified": {},
    }
    findings = w.Findings()
    findings.add(
        "BLOCKED",
        "watcher_release_stale",
        "full technical summary that must remain in the event",
        expected="/release/old",
    )
    events = []
    notifications = []
    monkeypatch.setattr(w, "capture_snapshot", lambda *_args: snapshot)
    monkeypatch.setattr(w, "state_append", lambda _path, payload: events.append(json.loads(payload)))
    monkeypatch.setattr(w, "log", lambda *_args: None)
    monkeypatch.setattr(w, "notify", lambda _state, _route, text, _dedupe: notifications.append(text))
    monkeypatch.setattr(w, "RELEASE_SHA", "ee2d0b1d7292cae1b6195f8e2045308a818272c3")

    w.handle_blocked(state, findings, {"verdict": "BLOCKED"})

    assert state["blocked"]["watcher_release_stale"]["summary"] == (
        "full technical summary that must remain in the event"
    )
    assert state["blocked"]["watcher_release_stale"]["snapshot"] == str(snapshot)
    assert events[0]["key"] == "watcher_release_stale"
    assert events[0]["detail"] == {"expected": "/release/old"}
    assert events[0]["snapshot"] == str(snapshot)
    assert notifications == [
        w._blocked_discord_text("watcher_release_stale", findings.blocked()[0], str(snapshot))
    ]


def test_cleared_blocker_sends_a_cleared_notification(monkeypatch):
    first = w.iso(w.now_utc() - w.timedelta(minutes=7))
    state = {
        "blocked": {"alert_non200": {"first_utc": first, "summary": "x", "snapshot": "/s"}},
        "blocked_last_notified": {"alert_non200": first},
        "notified": {"blocked:alert_non200": first},
    }
    notifications = []
    monkeypatch.setattr(w, "log", lambda *_args: None)
    monkeypatch.setattr(w, "notify", lambda _state, route, text, _dedupe: notifications.append((route, text)))
    monkeypatch.setattr(w, "RELEASE_SHA", "5115b780f2b8")

    w.handle_blocked(state, w.Findings(), {"verdict": "OK"})

    assert state["blocked"] == {}
    assert notifications[0][0] == "DISCORD_ROUTE_ERROR"
    assert notifications[0][1].splitlines()[0] == "✅ Resolved: Bot is rejecting TradingView alerts"
    assert "Lasted: 7 min" in notifications[0][1]
    assert "What to do: nothing" in notifications[0][1]


def _memory_tick(*, swap_in=0.0, swap_out=0.0):
    return {"memory_fixed": {"avail_mb": 924, "swap_used_mb": 259, "rss_mb": 341.0,
                              "swapin_mb_since_last_tick": swap_in,
                              "swapout_mb_since_last_tick": swap_out}}


def test_recovered_memory_pressure_is_not_presented_as_active(monkeypatch):
    monkeypatch.setattr(w, "RELEASE_SHA", "899a524aad82")
    monkeypatch.setattr(w, "_largest_rss_process", lambda: ("futures-bot", 341.0))
    text = w._memory_discord_text("RECOVERED", "swap_pressure_warning", None, _memory_tick())
    assert text.splitlines()[0] == "✅ Server memory is back to normal"
    assert "Was: Server is short of memory" in text
    assert "Swapping to disk now: no" in text
    assert "Biggest program: futures-bot (341 MB)" in text
    assert "What to do: nothing" in text
    for jargon in ("MiB", "RSS", "OOM"):
        assert jargon not in text


def test_ongoing_memory_pressure_remains_warning(monkeypatch):
    monkeypatch.setattr(w, "RELEASE_SHA", "899a524aad82")
    monkeypatch.setattr(w, "_largest_rss_process", lambda: ("alert-ranker", 288.4))
    finding = {"summary": "swap activity 0.0 MB in / 119.2 MB out since last tick", "detail": {}}
    text = w._memory_discord_text("WARNING", "swap_pressure_warning", finding,
                                  _memory_tick(swap_out=119.2), "/snap")
    assert text.splitlines()[0] == "⚠️ Server is short of memory"
    assert "Free memory: 924 MB" in text
    assert "Bot is using: 341 MB" in text
    assert "Swapping to disk now: yes, 119 MB in the last 5 min" in text
    assert "Biggest program: alert-ranker (288 MB)" in text
    assert "back to normal" not in text


def test_memory_warning_episode_emits_one_recovery_notice(monkeypatch):
    state = {"memory_fixed_warnings": {"swap_pressure_warning": {
        "active": True, "first_utc": "2026-09-15T20:00:00Z", "summary": "pressure", "snapshot": "/snap"
    }}, "notified": {}}
    sent = []
    monkeypatch.setattr(w, "log", lambda *_args: None)
    monkeypatch.setattr(w, "notify", lambda _state, route, text, key: sent.append((route, text, key)))
    monkeypatch.setattr(w, "now_utc", lambda: w._ts("2026-09-15T21:00:00Z"))
    monkeypatch.setattr(w, "_largest_rss_process", lambda: None)
    w.handle_memory_fixed_warnings(state, w.Findings(), _memory_tick())
    w.handle_memory_fixed_warnings(state, w.Findings(), _memory_tick())
    assert len(sent) == 1
    assert sent[0][1].startswith("✅ Server memory is back to normal")


def test_largest_rss_probe_is_display_only_and_fails_open(monkeypatch):
    monkeypatch.setattr(w, "run", lambda *_args, **_kwargs: (1, "ps unavailable"))
    assert w._largest_rss_process() is None
    text = w._memory_discord_text("RECOVERED", "swap_pressure_warning", None, _memory_tick())
    assert "Biggest program: unknown" in text


def test_largest_rss_probe_passes_the_real_read_only_allowlist(monkeypatch):
    # The probe used to be refused by run()'s allowlist, and that RuntimeError
    # crashed every tick that rendered a memory notice. Exercise the real run().
    calls = []

    def fake_subprocess_run(cmd, **_kwargs):
        calls.append(cmd)
        return w.subprocess.CompletedProcess(cmd, 0, stdout="python3 350000\nbash 4000\n", stderr="")

    monkeypatch.setattr(w.subprocess, "run", fake_subprocess_run)
    assert w._largest_rss_process() == ("python3", 341.8)
    assert calls == [["ps", "-eo", "comm=,rss=", "--sort=-rss"]]


def test_largest_rss_probe_fails_open_when_run_raises(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise RuntimeError("command not in read-only allowlist")

    monkeypatch.setattr(w, "run", refuse)
    assert w._largest_rss_process() is None
    text = w._memory_discord_text("RECOVERED", "swap_pressure_warning", None, _memory_tick())
    assert "Biggest program: unknown" in text


def test_feed_status_is_readable_for_healthy_and_stale(monkeypatch):
    monkeypatch.setattr(w, "now_utc", lambda: w._ts("2026-09-15T21:00:00Z"))
    lanes = {"inventory": {}, "newest_5m_mnq_bar_mtime": "2026-09-15T20:55:00Z",
             "five_min_feed_stalled": False}
    healthy = w._daily_discord_text("DAILY PASS", "2026-09-15", {}, 0, {}, [], "/f", lanes)
    assert "**Newest price bar:** 4:55 PM ET (5 min ago)" in healthy
    assert "2026-09-15T20:55:00Z" not in healthy
    assert "5m feed stalled: False" not in healthy
    lanes["five_min_feed_stalled"] = True
    stale = w._daily_discord_text("DAILY BLOCKED", "2026-09-15", {}, 0, {}, ["feed stale"], "/f", lanes)
    assert (f"**5-minute prices:** stopped — newest bar 4:55 PM ET (5 min ago) "
            f"(alarm after {w.LANE_STALL_MIN} min)") in stale


def test_campaign_populations_are_strategy_variant_specific_and_zero_visible():
    pops = {
        "vwap_hold/control": {"candidates": 9, "resolved_filled_economic": 2, "distinct_trading_days": 3},
        "vwap_hold/modified": {"candidates": 8, "resolved_filled_economic": 1, "distinct_trading_days": 3},
        "orb_reclaim/control": {"candidates": 0, "resolved_filled_economic": 0, "distinct_trading_days": 0},
        "orb_reclaim/modified": {"candidates": 4, "resolved_filled_economic": 1, "distinct_trading_days": 2},
        "vwap_rejection/observer": {"candidates": 5, "resolved_filled_economic": 0, "distinct_trading_days": 2},
    }
    text = w._daily_discord_text("DAILY PASS", "2026-09-15", {}, 26, pops, [], "/f")
    assert "**Forward test:** 5 of 5 versions reporting, 26 records so far" in text
    # a version with zero setups stays visible, by its plain name
    assert "**No setups yet:** Back inside the opening range (original rules)" in text
    assert "**Progress to review:** 0 of 20 trading days, 0 of 30 filled trades (slowest version)" in text
    for internal in ("vwap_hold", "orb_reclaim", "control", "modified", "cand ", "arms"):
        assert internal not in text
