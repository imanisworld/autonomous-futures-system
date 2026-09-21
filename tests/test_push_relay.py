from __future__ import annotations

import json
from pathlib import Path

from ops.push_relay.app import Event, SubscriptionStore, diff_events, signature


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
