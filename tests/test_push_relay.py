from __future__ import annotations

import json
from pathlib import Path

from datetime import datetime
from zoneinfo import ZoneInfo

from ops.push_relay.app import Event, SubscriptionStore, daily_due, daily_summary, diff_events, signature

ET = ZoneInfo("America/New_York")


def today(**over):
    base = {
        "date": "2026-09-21",
        "has_open_position": False,
        "open_position": None,
        "trade_count": 0,
        "wins": 0,
        "losses": 0,
        "realized_pnl_dollars": 0.0,
        "consecutive_losses": 0,
        "max_consecutive_losses": 9999,
    }
    base.update(over)
    return base


def test_first_observation_emits_nothing():
    assert diff_events(None, signature(today())) == []
    assert diff_events({}, signature(today())) == []


def test_position_open_then_close_with_outcome():
    a = signature(today())
    b = signature(today(has_open_position=True, open_position={"instrument": "MNQ", "direction": "LONG", "entry_price": 30100.25}))
    ev = diff_events(a, b)
    assert [e.title for e in ev] == ["Position opened"]
    assert "MNQ LONG @ 30100.25" in ev[0].body
    assert ev[0].url == "/futures"

    c = signature(today(trade_count=1, wins=1, realized_pnl_dollars=42.5))
    ev = diff_events(b, c)
    assert len(ev) == 1
    assert ev[0].title == "Position closed · WIN"
    assert "+$42.50" in ev[0].body
    assert ev[0].tag == "position"


def test_resolved_without_seeing_open_position():
    a = signature(today())
    b = signature(today(trade_count=1, losses=1, realized_pnl_dollars=-113.0))
    ev = diff_events(a, b)
    assert [e.title for e in ev] == ["Trade resolved"]
    assert "+0W / +1L" in ev[0].body
    assert "-$113.00" in ev[0].body


def test_daily_lock_fires_once_at_threshold():
    a = signature(today(consecutive_losses=2, max_consecutive_losses=3))
    b = signature(today(consecutive_losses=3, max_consecutive_losses=3, losses=3, trade_count=3))
    titles = [e.title for e in diff_events(a, b)]
    assert "Daily lock" in titles
    # already locked → no repeat
    assert "Daily lock" not in [e.title for e in diff_events(b, b)]
    # sentinel 9999 never locks
    x = signature(today(consecutive_losses=3, losses=3, trade_count=3))
    assert "Daily lock" not in [e.title for e in diff_events(signature(today()), x)]


def test_new_trading_day_resets_quietly():
    a = signature(today(trade_count=3, wins=2, losses=1))
    b = signature(today(date="2026-09-22"))
    assert diff_events(a, b) == []


def test_no_change_no_events():
    a = signature(today(trade_count=1, wins=1))
    assert diff_events(a, dict(a)) == []


def test_store_validates_persists_and_caps(tmp_path: Path):
    store = SubscriptionStore(tmp_path / "subs.json")
    good = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "P", "auth": "A"}}
    assert store.add(good, ua="Safari") is True
    assert store.add({"endpoint": "http://insecure", "keys": {"p256dh": "P", "auth": "A"}}) is False
    assert store.add({"endpoint": "https://x", "keys": {}}) is False
    assert len(store) == 1
    assert store.get("https://push.example/abc") == good
    # persisted + reloadable
    again = SubscriptionStore(tmp_path / "subs.json")
    assert again.all() == [good]
    assert json.loads((tmp_path / "subs.json").read_text())["https://push.example/abc"]["ua"] == "Safari"
    assert again.remove("https://push.example/abc") is True
    assert again.remove("https://push.example/abc") is False
    assert len(again) == 0


def test_relay_never_imports_bot_or_broker_code():
    src = Path("ops/push_relay/app.py").read_text()
    for forbidden in ("from webhook", "import webhook", "from execution", "from risk", "from strategy", "tradovate", "webull"):
        assert forbidden not in src.lower(), forbidden


def test_event_is_frozen_and_defaults_url():
    e = Event(title="t", body="b", tag="x")
    assert e.url == "/"


def test_daily_summary_lines():
    e = daily_summary(today(trade_count=3, wins=2, losses=1, realized_pnl_dollars=94.0))
    assert e.title == "Close · 2026-09-21"
    assert e.body == "3 trades · 2W-1L · P&L +$94.00"
    assert e.tag == "daily" and e.url == "/journal"

    quiet = daily_summary(today(top_no_trade_reasons=[{"reason": "Market condition is RANGE_BOUND, not TRENDING.", "count": 5}]))
    assert quiet.body.startswith("0 trades · P&L $0.00 · top block: Market condition is RANGE_BOUND")

    held = daily_summary(today(trade_count=1, has_open_position=True, open_position={"instrument": "MNQ", "direction": "LONG"}))
    assert held.body.endswith("open MNQ LONG")


def test_daily_due_once_per_weekday_after_time():
    mon_early = datetime(2026, 9, 21, 16, 0, tzinfo=ET)
    mon_late = datetime(2026, 9, 21, 16, 15, tzinfo=ET)
    mon_later = datetime(2026, 9, 21, 22, 0, tzinfo=ET)
    sat = datetime(2026, 9, 26, 17, 0, tzinfo=ET)
    assert daily_due(mon_early, None, "16:15") is False
    assert daily_due(mon_late, None, "16:15") is True
    assert daily_due(mon_later, "2026-09-21", "16:15") is False   # already sent today
    assert daily_due(mon_later, "2026-09-18", "16:15") is True    # relay was down at 16:15 → catch up
    assert daily_due(sat, None, "16:15") is False
    assert daily_due(mon_late, None, "") is False
    assert daily_due(mon_late, None, "bad") is False


def test_poll_reuses_one_http_client():
    """A new AsyncClient per poll leaked ~0.7 MB/poll on the box (SSL context)."""
    import asyncio

    from ops.push_relay import app as relay

    relay._http_client = None
    first = relay._client()
    assert relay._client() is first
    asyncio.run(relay._shutdown())
    assert relay._http_client is None and first.is_closed
    second = relay._client()
    assert second is not first
    asyncio.run(relay._shutdown())
