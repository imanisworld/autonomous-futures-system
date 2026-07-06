"""
tests/test_reconciler.py

Phantom-position reconciler: clears a journal-open / broker-flat mismatch ONLY
when the broker is authenticated and definitively flat and the position is stale.
On any uncertainty it must do NOTHING (never book a close on a guess).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from execution.broker_interface import Fill
from journal.journal_logger import JournalLogger
from webhook.reconciler import reconcile_open_position

_NOW = datetime(2026, 6, 5, 16, 0, tzinfo=timezone.utc)


class _FakeBroker:
    def __init__(self, authed=True, position=None, position_confirmed=True,
                 entry_filled=None, resolve_fill=None):
        self._authed = authed
        self._position = position
        self._position_confirmed = position_confirmed
        self._entry_filled = entry_filled
        self._resolve_fill = resolve_fill
        self._last_position = None
        self._last_order_ids = None
        self.resolve_calls = 0

    def _authenticate(self):
        return self._authed

    def get_position_snapshot(self):
        return self._position_confirmed, self._position

    def entry_order_filled(self, order_id):
        return self._entry_filled

    def resolve_position(self):
        self.resolve_calls += 1
        return self._resolve_fill


def _seed_open(log_dir, *, age_min=30, order_ids=None):
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
    if order_ids is not None:
        j._append({
            "ts": (_NOW - timedelta(minutes=age_min - 1)).isoformat(),
            "type": "ORDER_IDS",
            "instrument": "MNQ",
            "session": "new_york",
            "order_ids": order_ids,
        }, _NOW.date())
    return j


_IDS = {"instrument": "MNQ", "entry": 111, "target": 222, "stop": 333}


def _win_fill():
    return Fill(
        instrument="MNQ", direction="SHORT", contracts=1,
        entry_price=30000.0, exit_price=29976.0, exit_reason="TARGET_HIT",
        result="WIN", pnl_ticks=96.0, pnl_dollars=48.0,
    )


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


def test_does_nothing_when_position_read_is_unconfirmed(monkeypatch, tmp_path, config):
    _tradovate(monkeypatch)
    j = _seed_open(tmp_path, age_min=30)
    res = reconcile_open_position(
        config,
        str(tmp_path),
        now=_NOW,
        broker=_FakeBroker(authed=True, position=None, position_confirmed=False),
    )
    assert res["action"] == "broker_position_unconfirmed"
    assert j.get_open_position(_NOW.date()) is not None


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


# ── Completed-trade guard (2026-07-06 erased-win incident) ─────────────────────

def test_resolves_completed_trade_instead_of_clearing(monkeypatch, tmp_path, config):
    """Entry has fills + broker flat = the trade COMPLETED between bar resolves.
    The sweep must book the real outcome, never CANCELLED $0."""
    _tradovate(monkeypatch)
    j = _seed_open(tmp_path, age_min=30, order_ids=_IDS)
    broker = _FakeBroker(authed=True, position=None,
                         entry_filled=True, resolve_fill=_win_fill())
    res = reconcile_open_position(config, str(tmp_path), now=_NOW, broker=broker)
    assert res["action"] == "resolved_completed_trade"
    assert res["result"] == "WIN"
    assert j.get_open_position(_NOW.date()) is None          # closed
    # The journaled outcome is the REAL fill, not a cancel.
    entries = j._read_entries(j._journal_path(_NOW.date()))
    outcome = [e for e in entries if e.get("type") == "OUTCOME"][-1]["outcome"]
    assert outcome["result"] == "WIN"
    assert outcome["pnl_dollars"] == 48.0
    assert outcome["exit_reason"] == "TARGET_HIT"
    # Resolver got the journal-restored context (order-id exit attribution).
    assert broker._last_order_ids == _IDS
    assert broker._last_position.direction == "SHORT"


def test_clears_phantom_when_entry_never_filled(monkeypatch, tmp_path, config):
    """Zero fills on the entry order = true phantom → legacy CANCELLED clear."""
    _tradovate(monkeypatch)
    j = _seed_open(tmp_path, age_min=30, order_ids=_IDS)
    broker = _FakeBroker(authed=True, position=None, entry_filled=False)
    res = reconcile_open_position(config, str(tmp_path), now=_NOW, broker=broker)
    assert res["action"] == "reconciled"
    assert broker.resolve_calls == 0                          # never resolves a no-fill
    entries = j._read_entries(j._journal_path(_NOW.date()))
    outcome = [e for e in entries if e.get("type") == "OUTCOME"][-1]["outcome"]
    assert outcome["result"] == "CANCELLED"


def test_unreadable_fill_state_leaves_position_untouched(monkeypatch, tmp_path, config):
    """Fill list unreadable (None) = uncertainty → do nothing this sweep."""
    _tradovate(monkeypatch)
    j = _seed_open(tmp_path, age_min=30, order_ids=_IDS)
    broker = _FakeBroker(authed=True, position=None, entry_filled=None)
    res = reconcile_open_position(config, str(tmp_path), now=_NOW, broker=broker)
    assert res["action"] == "entry_fill_unconfirmed"
    assert j.get_open_position(_NOW.date()) is not None       # untouched


def test_entry_filled_but_unresolvable_stays_open(monkeypatch, tmp_path, config):
    """Fills exist but exit attribution isn't readable yet → leave the position
    for the next bar resolve/sweep; never fall through to a CANCELLED clear."""
    _tradovate(monkeypatch)
    j = _seed_open(tmp_path, age_min=30, order_ids=_IDS)
    broker = _FakeBroker(authed=True, position=None,
                         entry_filled=True, resolve_fill=None)
    res = reconcile_open_position(config, str(tmp_path), now=_NOW, broker=broker)
    assert res["action"] == "entry_filled_unresolved"
    assert broker.resolve_calls == 3                          # engages the fail ladder
    assert j.get_open_position(_NOW.date()) is not None       # untouched


def test_no_order_ids_still_clears_phantom(monkeypatch, tmp_path, config):
    """No ORDER_IDS row (e.g. shadow-day would-be trades, pre-persistence
    journals) → the legacy flat-clear path is unchanged."""
    _tradovate(monkeypatch)
    j = _seed_open(tmp_path, age_min=30)                      # no order ids seeded
    broker = _FakeBroker(authed=True, position=None, entry_filled=None)
    res = reconcile_open_position(config, str(tmp_path), now=_NOW, broker=broker)
    assert res["action"] == "reconciled"
    assert j.get_open_position(_NOW.date()) is None
