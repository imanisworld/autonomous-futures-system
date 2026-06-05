"""
tests/test_reconciler.py

Phantom-position reconciler: clears a journal-open / broker-flat mismatch ONLY
when the broker is authenticated and definitively flat and the position is stale.
On any uncertainty it must do NOTHING (never book a close on a guess).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from journal.journal_logger import JournalLogger
from webhook.reconciler import reconcile_open_position

_NOW = datetime(2026, 6, 5, 16, 0, tzinfo=timezone.utc)


class _FakeBroker:
    def __init__(self, authed=True, position=None):
        self._authed = authed
        self._position = position

    def _authenticate(self):
        return self._authed

    def get_position(self):
        return self._position


def _seed_open(log_dir, *, age_min=30):
    j = JournalLogger(log_dir=str(log_dir))
    ts = (_NOW - timedelta(minutes=age_min)).isoformat()
    j._append({
        "ts": ts,
        "instrument": "MNQ",
        "session": "new_york",
        "decision": "TRADE",
        "risk_check": {"result": "APPROVED"},
        "setup": {"direction": "SHORT", "entry": 30000.0, "stop": 30008.0,
                  "target": 29976.0, "contracts": 1},
        "outcome": None,
    }, _NOW.date())
    return j


def _tradovate(monkeypatch):
    monkeypatch.setenv("BROKER", "tradovate")


def test_clears_phantom_when_broker_flat(monkeypatch, tmp_path, config):
    _tradovate(monkeypatch)
    j = _seed_open(tmp_path, age_min=30)
    assert j.get_open_position(_NOW.date()) is not None      # phantom present
    res = reconcile_open_position(config, str(tmp_path), now=_NOW,
                                  broker=_FakeBroker(authed=True, position=None))
    assert res["action"] == "reconciled"
    assert j.get_open_position(_NOW.date()) is None          # cleared


def test_leaves_position_when_broker_still_holds_it(monkeypatch, tmp_path, config):
    _tradovate(monkeypatch)
    j = _seed_open(tmp_path, age_min=30)
    res = reconcile_open_position(
        config, str(tmp_path), now=_NOW,
        broker=_FakeBroker(authed=True, position=SimpleNamespace(open=True)),
    )
    assert res["action"] == "broker_has_position"
    assert j.get_open_position(_NOW.date()) is not None      # untouched


def test_does_nothing_when_broker_unauthenticated(monkeypatch, tmp_path, config):
    _tradovate(monkeypatch)
    j = _seed_open(tmp_path, age_min=30)
    res = reconcile_open_position(config, str(tmp_path), now=_NOW,
                                  broker=_FakeBroker(authed=False))
    assert res["action"] == "broker_unauthenticated"
    assert j.get_open_position(_NOW.date()) is not None      # uncertainty → untouched


def test_does_not_touch_recent_position(monkeypatch, tmp_path, config):
    _tradovate(monkeypatch)
    j = _seed_open(tmp_path, age_min=2)                      # inside settle window
    res = reconcile_open_position(config, str(tmp_path), now=_NOW,
                                  broker=_FakeBroker(authed=True, position=None))
    assert res["action"] == "too_recent"
    assert j.get_open_position(_NOW.date()) is not None


def test_noop_when_no_open_position(monkeypatch, tmp_path, config):
    _tradovate(monkeypatch)
    JournalLogger(log_dir=str(tmp_path))  # empty journal
    res = reconcile_open_position(config, str(tmp_path), now=_NOW,
                                  broker=_FakeBroker(authed=True, position=None))
    assert res["action"] == "none"


def test_skips_paper_broker(monkeypatch, tmp_path, config):
    monkeypatch.setenv("BROKER", "paper")
    j = _seed_open(tmp_path, age_min=30)
    res = reconcile_open_position(config, str(tmp_path), now=_NOW,
                                  broker=_FakeBroker(authed=True, position=None))
    assert res["action"] == "skip_non_tradovate"
    assert j.get_open_position(_NOW.date()) is not None      # paper resolves on bars
