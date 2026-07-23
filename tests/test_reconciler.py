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
from notifications.system_notifier import SystemNotificationResult
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


def _seed_open(log_dir, *, age_min=30, order_ids=None, for_date=None):
    j = JournalLogger(log_dir=str(log_dir))
    for_date = for_date or _NOW.date()
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
    }, for_date)
    if order_ids is not None:
        j._append({
            "ts": (_NOW - timedelta(minutes=age_min - 1)).isoformat(),
            "type": "ORDER_IDS",
            "instrument": "MNQ",
            "session": "new_york",
            "order_ids": order_ids,
        }, for_date)
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


# ── notify_system() result observability (silent disabled/missing/failed → logged) ──

def _patch_notify_system(monkeypatch, result: SystemNotificationResult):
    import notifications.system_notifier as system_notifier_module
    monkeypatch.setattr(system_notifier_module, "notify_system", lambda *a, **k: result)


def test_phantom_clear_logs_success_when_discord_sends(monkeypatch, tmp_path, config, caplog):
    _tradovate(monkeypatch)
    _patch_notify_system(monkeypatch, SystemNotificationResult(sent=True, reason="sent"))
    j = _seed_open(tmp_path, age_min=30)
    with caplog.at_level("INFO"):
        res = reconcile_open_position(config, str(tmp_path), now=_NOW,
                                      broker=_FakeBroker(authed=True, position=None))
    assert res["action"] == "reconciled"                     # trading behavior unchanged
    assert j.get_open_position(_NOW.date()) is None           # journal outcome unchanged
    assert "Phantom-clear Discord notification sent" in caplog.text
    assert "NOT sent" not in caplog.text


def test_phantom_clear_logs_reason_when_discord_not_sent(monkeypatch, tmp_path, config, caplog):
    _tradovate(monkeypatch)
    _patch_notify_system(
        monkeypatch, SystemNotificationResult(sent=False, reason="missing_webhook_url")
    )
    j = _seed_open(tmp_path, age_min=30)
    with caplog.at_level("WARNING"):
        res = reconcile_open_position(config, str(tmp_path), now=_NOW,
                                      broker=_FakeBroker(authed=True, position=None))
    assert res["action"] == "reconciled"                     # trading behavior unchanged
    assert j.get_open_position(_NOW.date()) is None           # journal outcome unchanged
    assert "Phantom-clear Discord notification NOT sent (reason=missing_webhook_url)" in caplog.text


def test_resolved_completed_trade_logs_success_when_discord_sends(
    monkeypatch, tmp_path, config, caplog
):
    _tradovate(monkeypatch)
    _patch_notify_system(monkeypatch, SystemNotificationResult(sent=True, reason="sent"))
    j = _seed_open(tmp_path, age_min=30, order_ids=_IDS)
    broker = _FakeBroker(authed=True, position=None, entry_filled=True, resolve_fill=_win_fill())
    with caplog.at_level("INFO"):
        res = reconcile_open_position(config, str(tmp_path), now=_NOW, broker=broker)
    assert res["action"] == "resolved_completed_trade"       # trading behavior unchanged
    assert res["result"] == "WIN"
    entries = j._read_entries(j._journal_path(_NOW.date()))
    outcome = [e for e in entries if e.get("type") == "OUTCOME"][-1]["outcome"]
    assert outcome["result"] == "WIN"                          # journal outcome unchanged
    assert "Reconciled-trade Discord notification sent" in caplog.text


def test_resolved_completed_trade_logs_reason_when_discord_not_sent(
    monkeypatch, tmp_path, config, caplog
):
    _tradovate(monkeypatch)
    _patch_notify_system(monkeypatch, SystemNotificationResult(sent=False, reason="disabled"))
    j = _seed_open(tmp_path, age_min=30, order_ids=_IDS)
    broker = _FakeBroker(authed=True, position=None, entry_filled=True, resolve_fill=_win_fill())
    with caplog.at_level("WARNING"):
        res = reconcile_open_position(config, str(tmp_path), now=_NOW, broker=broker)
    assert res["action"] == "resolved_completed_trade"       # trading behavior unchanged
    entries = j._read_entries(j._journal_path(_NOW.date()))
    outcome = [e for e in entries if e.get("type") == "OUTCOME"][-1]["outcome"]
    assert outcome["result"] == "WIN"                          # journal outcome unchanged
    assert "Reconciled-trade Discord notification NOT sent (reason=disabled)" in caplog.text


# ── prior-day open positions (walk-back) + orphan-open naked alert ────────────
# The 2026-07-21 MES orphan: position opened Monday, children Day-expired at
# session close, position sat naked while (a) this sweep couldn't see it after
# midnight (today-only journal check) and (b) the broker-has-position branch
# returned silently. These lock in both fixes.

class _OrphanBroker(_FakeBroker):
    """Broker that holds an open position and reports a child-liveness census."""
    def __init__(self, census, **kw):
        super().__init__(authed=True, position=SimpleNamespace(open=True), **kw)
        self._census = census

    def count_working_children(self):
        return self._census


def test_walkback_sees_prior_day_open_position(monkeypatch, tmp_path, config):
    """A position opened YESTERDAY must be visible to today's sweep — the
    today-only journal check made the Jul 21 MES orphan invisible from 00:00
    Jul 22 onward (action was 'none' every sweep). Prior-day positions route
    through the resolver, never the phantom-clear (see the stale-fill tests
    below); here the resolver is uncertain, so the sweep reports that instead
    of 'none' and leaves the position open."""
    _tradovate(monkeypatch)
    yesterday = _NOW.date() - timedelta(days=1)
    j = _seed_open(tmp_path, age_min=30, for_date=yesterday)
    assert j.get_open_position(yesterday) is not None

    res = reconcile_open_position(config, str(tmp_path), now=_NOW,
                                  broker=_FakeBroker(authed=True, position=None))

    assert res["action"] == "entry_filled_unresolved"        # seen, not 'none'
    assert j.get_open_position(yesterday) is not None        # never guess-cleared


def test_orphan_open_alerts_when_zero_children_working(monkeypatch, tmp_path, config):
    """Journal open + broker open + zero working children → loud alert, no
    journal mutation, no order action."""
    _tradovate(monkeypatch)
    j = _seed_open(tmp_path, age_min=90, order_ids=_IDS)
    sent = {}

    def _capture(msg, **kw):
        sent["msg"] = msg
        return SystemNotificationResult(sent=True, reason="sent")

    import notifications.system_notifier as system_notifier_module
    monkeypatch.setattr(system_notifier_module, "notify_system", _capture)

    census = {"working": 0, "checked": 2,
              "states": {"target": "expired", "stop": "expired"}}
    res = reconcile_open_position(config, str(tmp_path), now=_NOW,
                                  broker=_OrphanBroker(census))

    assert res["action"] == "orphan_open_alerted"
    assert "NAKED" in sent["msg"] and "expired" in sent["msg"]
    assert j.get_open_position(_NOW.date()) is not None      # journal untouched


def test_orphan_alert_repeats_every_sweep(monkeypatch, tmp_path, config):
    """The alert must NOT be one-shot — an unheeded naked position stays loud."""
    _tradovate(monkeypatch)
    _seed_open(tmp_path, age_min=90, order_ids=_IDS)
    calls = []

    import notifications.system_notifier as system_notifier_module
    monkeypatch.setattr(
        system_notifier_module, "notify_system",
        lambda msg, **kw: (calls.append(msg), SystemNotificationResult(True, "sent"))[1],
    )

    census = {"working": 0, "checked": 2,
              "states": {"target": "expired", "stop": "expired"}}
    for _ in range(3):
        res = reconcile_open_position(config, str(tmp_path), now=_NOW,
                                      broker=_OrphanBroker(census))
        assert res["action"] == "orphan_open_alerted"
    assert len(calls) == 3


def test_broker_open_with_working_children_stays_quiet(monkeypatch, tmp_path, config):
    _tradovate(monkeypatch)
    _seed_open(tmp_path, age_min=90, order_ids=_IDS)
    census = {"working": 2, "checked": 2,
              "states": {"target": "working", "stop": "working"}}
    res = reconcile_open_position(config, str(tmp_path), now=_NOW,
                                  broker=_OrphanBroker(census))
    assert res["action"] == "broker_has_position"


def test_broker_open_with_unknowable_census_never_reads_as_naked(
    monkeypatch, tmp_path, config
):
    """census=None (ids missing / API blip) must NOT alert — uncertainty is
    never treated as naked."""
    _tradovate(monkeypatch)
    _seed_open(tmp_path, age_min=90, order_ids=_IDS)
    res = reconcile_open_position(config, str(tmp_path), now=_NOW,
                                  broker=_OrphanBroker(None))
    assert res["action"] == "broker_has_position"


def test_prior_day_position_never_phantom_cleared_on_missing_fills(
    monkeypatch, tmp_path, config
):
    """/fill/list is session-scoped: for a PRIOR-day position, a readable-but-
    empty fill list proves nothing. entry_filled=False must route through the
    resolver (real attribution / FORCE_CLOSE breakeven), never the CANCELLED
    phantom-clear — that would erase a real trade's outcome and trade count."""
    _tradovate(monkeypatch)
    yesterday = _NOW.date() - timedelta(days=1)
    j = _seed_open(tmp_path, age_min=30, order_ids=_IDS, for_date=yesterday)
    broker = _FakeBroker(authed=True, position=None,
                         entry_filled=False,          # aged-out fills say "no fill"
                         resolve_fill=_win_fill())
    res = reconcile_open_position(config, str(tmp_path), now=_NOW, broker=broker)
    assert res["action"] == "resolved_completed_trade"
    assert res["result"] == "WIN"                     # real outcome booked
    entries = j._read_entries(j._journal_path(yesterday))
    outcome = [e for e in entries if e.get("type") == "OUTCOME"][-1]["outcome"]
    assert outcome["result"] == "WIN"                 # landed in ITS OWN day
    assert j.get_open_position(yesterday) is None


def test_prior_day_position_left_open_when_resolver_uncertain(
    monkeypatch, tmp_path, config
):
    _tradovate(monkeypatch)
    yesterday = _NOW.date() - timedelta(days=1)
    j = _seed_open(tmp_path, age_min=30, order_ids=_IDS, for_date=yesterday)
    broker = _FakeBroker(authed=True, position=None,
                         entry_filled=False, resolve_fill=None)
    res = reconcile_open_position(config, str(tmp_path), now=_NOW, broker=broker)
    assert res["action"] == "entry_filled_unresolved"
    assert j.get_open_position(yesterday) is not None  # untouched


# ── End-to-end replay of the Jul 22/23 orphan conditions ──────────────────────
# Real TradovateBroker resolve/census logic + real reconciler + synthetic
# journal; only the HTTP layer is stubbed. Mirrors the offline replay run
# against the actual box journals on 2026-07-23 (operator-directed).

def _real_broker_e2e(monkeypatch, get_router):
    from execution.tradovate_broker import TradovateBroker, TradovateConfig
    broker = TradovateBroker(config=TradovateConfig())
    broker._account_id = 999
    monkeypatch.setattr(broker, "_authenticate", lambda: True)
    monkeypatch.setattr(broker, "_get", get_router)
    monkeypatch.setattr(broker, "_find_contract_id", lambda inst: 4399631)
    monkeypatch.setattr("execution.tradovate_broker.time.sleep", lambda *a, **k: None)
    return broker


def test_e2e_prior_day_orphan_books_force_close_in_one_sweep(
    monkeypatch, tmp_path, config
):
    """The flattened-orphan condition: prior-day journal-open, broker FLAT,
    every session-scoped list empty (fills aged out). One sweep must book
    FORCE_CLOSE_UNMATCHED/BREAKEVEN into the position's own journal day via
    the REAL resolve_position retry ladder (attempt 1→2→3 on ONE instance —
    the bar path can never get there because the runner rebuilds the broker
    each bar and resets the counter)."""
    _tradovate(monkeypatch)
    yesterday = _NOW.date() - timedelta(days=1)
    j = _seed_open(tmp_path, age_min=120, order_ids=_IDS, for_date=yesterday)

    def _get(path, **kw):
        if path.startswith(("/position/list", "/fill/list", "/order/list")):
            return []
        raise RuntimeError(f"unmocked GET {path}")

    import notifications.system_notifier as system_notifier_module
    monkeypatch.setattr(system_notifier_module, "notify_system",
                        lambda *a, **k: SystemNotificationResult(True, "sent"))

    broker = _real_broker_e2e(monkeypatch, _get)
    res = reconcile_open_position(config, str(tmp_path), now=_NOW, broker=broker)

    assert res["action"] == "resolved_completed_trade"
    assert res["result"] == "BREAKEVEN"
    entries = j._read_entries(j._journal_path(yesterday))
    outcome = [e for e in entries if e.get("type") == "OUTCOME"][-1]["outcome"]
    assert outcome["exit_reason"] == "FORCE_CLOSE_UNMATCHED"
    assert outcome["pnl_dollars"] == 0.0                  # never fabricates a price
    assert j.get_open_position(yesterday) is None
    assert not j.get_daily_state(yesterday).has_open_position


def test_e2e_naked_open_position_alerts_via_real_census(
    monkeypatch, tmp_path, config
):
    """The pre-flatten condition: prior-day journal-open, broker STILL HOLDS
    the position, both children Expired at the broker. The REAL census must
    classify it naked and the sweep must alert loudly without touching the
    journal."""
    _tradovate(monkeypatch)
    yesterday = _NOW.date() - timedelta(days=1)
    j = _seed_open(tmp_path, age_min=120, order_ids=_IDS, for_date=yesterday)

    def _get(path, **kw):
        if path.startswith("/position/list"):
            return [{"netPos": 1, "contractId": 4399631, "netPrice": 30000.0}]
        if path.startswith("/order/item?id=222"):
            return {"ordStatus": "Expired"}
        if path.startswith("/order/item?id=333"):
            return {"ordStatus": "Expired"}
        if path.startswith("/order/list"):
            return []
        raise RuntimeError(f"unmocked GET {path}")

    alerts = []
    import notifications.system_notifier as system_notifier_module
    monkeypatch.setattr(
        system_notifier_module, "notify_system",
        lambda msg, **kw: (alerts.append(msg), SystemNotificationResult(True, "sent"))[1],
    )

    broker = _real_broker_e2e(monkeypatch, _get)
    res = reconcile_open_position(config, str(tmp_path), now=_NOW, broker=broker)

    assert res["action"] == "orphan_open_alerted"
    assert res["states"] == {"target": "expired", "stop": "expired"}
    assert len(alerts) == 1 and "NAKED" in alerts[0]
    assert j.get_open_position(yesterday) is not None      # journal untouched
