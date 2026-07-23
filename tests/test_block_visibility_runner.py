"""End-to-end: the runner journals a BLOCK_VISIBILITY record on the single-
position block, WITHOUT changing the gate (still blocked, no evaluation) and
WITHOUT any order action.

Required tests #1, #5, #6 (2026-07-23):
  1. A normal open position produces a journaled resolving status.
  5. Decision evaluation remains blocked until state agreement.
  6. Visibility logging cannot submit/cancel/replace/flatten orders.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone

from journal.journal_logger import JournalLogger
from webhook.payload import AlertPayload
from webhook.runner import process_alert


def _payload(**ov):
    data = {
        "ticker": "MNQ1!", "timestamp": "2026-05-23T14:30:00+00:00", "timeframe": "15",
        "open": 19510.0, "high": 19560.0, "low": 19505.0, "close": 19550.0,
        "volume": 4200, "avg_volume": 3800, "vwap": 19495.0,
        "orb_high": 19498.0, "orb_low": 19462.0, "orb_status": "above",
        "market_condition": "TRENDING", "trend_direction": "UP", "trend_strength": "MODERATE",
        "previous_day_high": 19520.0, "previous_day_low": 19440.0, "previous_day_close": 19475.0,
    }
    data.update(ov)
    return AlertPayload(**data)


def _seed_open(journal, for_date):
    journal._append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "instrument": "MNQ", "session": "new_york", "decision": "TRADE",
        "market_condition": "TRENDING",
        "context": {"timestamp": f"{for_date.isoformat()}T14:25:00+00:00"},
        "setup": {"direction": "LONG", "entry": 19500.0, "stop": 19460.0,
                  "target": 19580.0, "rr_ratio": 2.0, "strategy": "orb_reclaim",
                  "notes": None, "contracts": 1},
        "risk_check": {"result": "APPROVED", "failed_rule": None, "reason": None},
        "outcome": None,
    }, for_date)
    journal.log_order_ids(instrument="MNQ", session="new_york",
                          order_ids={"entry": 111, "stop": 222, "target": 333},
                          for_date=for_date)


def test_block_still_blocks_and_journals_visibility(config, tmp_path, monkeypatch):
    monkeypatch.setenv("BROKER", "paper")
    log_dir = str(tmp_path / "logs")
    today = date(2026, 5, 23)
    journal = JournalLogger(log_dir=log_dir)
    _seed_open(journal, today)

    before = journal.get_daily_state(today)
    result = process_alert(_payload(), config=replace(config, paper_mode=True),
                           log_dir=log_dir, for_date=today)

    # #5: still blocked, evaluation did not run.
    assert result["decision"] == "BLOCKED_OPEN_POSITION"
    # #1: a classified visibility record is attached AND journaled.
    vis = result.get("block_visibility")
    assert vis is not None
    assert vis["classification"] in (
        "ACTIVE_POSITION_RESOLVING", "STALE_RESOLVE",
        "BROKER_LOCAL_STATE_DRIFT", "PIPELINE_BLOCKED",
    )
    assert vis["instrument"] == "MNQ"
    assert vis["local_state"] == "OPEN"
    entries = journal._read_entries(journal._journal_path(today))
    journaled = [e for e in entries if e.get("type") == "BLOCK_VISIBILITY"]
    assert len(journaled) == 1
    # The gate is unchanged: no new trade, slot intact.
    after = journal.get_daily_state(today)
    assert after.trade_count == before.trade_count
    assert after.has_open_position is True


def test_repeated_blocks_journal_each_bar_no_duplicate_outcomes(config, tmp_path, monkeypatch):
    """#6 corollary: many blocked bars each journal a visibility row and never
    fabricate an OUTCOME (no order/exit is booked by the visibility path)."""
    monkeypatch.setenv("BROKER", "paper")
    log_dir = str(tmp_path / "logs")
    today = date(2026, 5, 23)
    journal = JournalLogger(log_dir=log_dir)
    _seed_open(journal, today)

    # Distinct bar timestamps so each is a fresh bar (same bar → BLOCKED_DUPLICATE_BAR).
    for hhmm in ("14:30:00", "14:45:00", "15:00:00", "15:15:00", "15:30:00"):
        r = process_alert(_payload(timestamp=f"2026-05-23T{hhmm}+00:00"),
                          config=replace(config, paper_mode=True),
                          log_dir=log_dir, for_date=today)
        assert r["decision"] == "BLOCKED_OPEN_POSITION"

    entries = journal._read_entries(journal._journal_path(today))
    assert len([e for e in entries if e.get("type") == "BLOCK_VISIBILITY"]) == 5
    # No OUTCOME was ever written by the block path.
    assert [e for e in entries if e.get("type") == "OUTCOME"] == []
    # Exactly one TRADE (the seed); the block never adds trades.
    trades = [e for e in entries if e.get("decision") == "TRADE"]
    assert len(trades) == 1


def test_visibility_module_has_no_order_surface():
    """#6: the visibility module cannot place/cancel/replace/flatten — it imports
    nothing from the broker/order/execution layer at all. Checked via the AST
    (imports), not source substrings, so the module's own prose can't false-trip
    or false-pass it."""
    import ast
    import inspect
    from ops import block_visibility as mod

    tree = ast.parse(inspect.getsource(mod))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    # The module must depend only on the stdlib — no execution/broker/order deps.
    for mod_name in imported:
        top = mod_name.split(".")[0]
        assert top in {"datetime", "typing", "__future__"}, \
            f"visibility module imports non-stdlib dependency: {mod_name}"
    # And no attribute call whose name is an order verb (belt-and-suspenders).
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert called.isdisjoint({
        "place_order", "placeOSO", "cancel_order", "cancelorder", "modifyorder",
        "liquidate_position", "flatten_position", "execute_bracket", "replace_stop",
    })
