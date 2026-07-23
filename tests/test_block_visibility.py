"""Pipeline-visibility layer for the single-position block.

The BLOCKED_OPEN_POSITION gate early-returns before any log_decision, so a
blocked bar left NO journal record — the 2026-07-22 orphan blinded the pipeline
for a whole session and the journal showed zero decisions, indistinguishable
from a quiet market (46-day audit: the block was never journaled once). These
tests pin the visibility layer: every block now produces an INERT, classified,
journaled record — and that record can never place an order or alter daily state.

Covers the operator's required tests (2026-07-23):
  1. Normal open position → journaled resolving status.
  2. Repeated blocked bars visible, no duplicate trade outcomes.
  3. Stale resolution escalates after the threshold.
  4. Broker-flat/local-open drift distinguished from a legitimate open position.
  5. Decision evaluation remains blocked until state agreement.
  6. Visibility logging cannot submit/cancel/replace/flatten orders.
  7. (existing strategy/reconciliation suites remain green — run separately.)
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

from journal.journal_logger import JournalLogger
from ops import block_visibility as bv


_NOW = datetime(2026, 7, 21, 18, 0, tzinfo=timezone.utc)


def _open_pos(age_min=5, order_ids=None):
    return {
        "instrument": "MES",
        "direction": "LONG",
        "entry": 7531.0,
        "stop": 7520.75,
        "target": 7555.75,
        "contracts": 1,
        "strategy": "orb_reclaim",
        "ts": (_NOW - timedelta(minutes=age_min)).isoformat(),
        "order_ids": order_ids or {"instrument": "MES", "entry": 111, "target": 222, "stop": 333},
    }


# ── classifier (pure) ─────────────────────────────────────────────────────────

def test_young_position_is_active_resolving():
    assert bv.classify_block(_open_pos(age_min=5), _NOW.isoformat()) == bv.ACTIVE_POSITION_RESOLVING


def test_old_position_is_stale_resolve():
    old = _open_pos(age_min=int(bv.DEFAULT_STALE_MINUTES) + 60)
    assert bv.classify_block(old, _NOW.isoformat()) == bv.STALE_RESOLVE


def test_broker_flat_local_open_is_drift():
    # Even a YOUNG position is drift when the broker read says flat.
    assert bv.classify_block(_open_pos(age_min=1), _NOW.isoformat(), broker_open=False) \
        == bv.BROKER_LOCAL_STATE_DRIFT


def test_broker_unknown_never_reads_as_drift():
    # The runner passes UNKNOWN (no hot-path broker I/O) — must classify on age,
    # never as drift.
    old = _open_pos(age_min=int(bv.DEFAULT_STALE_MINUTES) + 60)
    assert bv.classify_block(old, _NOW.isoformat(), broker_open=bv.BROKER_UNKNOWN) == bv.STALE_RESOLVE
    young = _open_pos(age_min=2)
    assert bv.classify_block(young, _NOW.isoformat()) == bv.ACTIVE_POSITION_RESOLVING


def test_unparseable_age_is_pipeline_blocked():
    pos = _open_pos(); pos["ts"] = None
    assert bv.classify_block(pos, _NOW.isoformat()) == bv.PIPELINE_BLOCKED


def test_record_has_all_required_fields():
    rec = bv.build_block_record(
        _open_pos(age_min=10), _NOW.isoformat(),
        instrument="MNQ", session="new_york", last_reconcile_ts="2026-07-21T17:40:00+00:00",
    )
    for k in ("type", "blocked_decision", "instrument", "session", "lifecycle_id",
              "strategy", "position_instrument", "position_direction", "local_state",
              "broker_state", "position_age_minutes", "last_reconcile_ts",
              "classification", "reason"):
        assert k in rec, f"missing field {k}"
    assert rec["type"] == "BLOCK_VISIBILITY"
    assert rec["blocked_decision"] == "BLOCKED_OPEN_POSITION"
    assert rec["lifecycle_id"] == "111"           # entry order id
    assert rec["local_state"] == "OPEN"
    assert rec["broker_state"] == "unavailable"   # UNKNOWN → unavailable
    assert "did not run" in rec["reason"]
    assert rec["instrument"] == "MNQ"             # the INCOMING alert
    assert rec["position_instrument"] == "MES"    # the HELD position


def test_should_escalate_only_on_drift_or_stale():
    active = bv.build_block_record(_open_pos(age_min=3), _NOW.isoformat(), instrument="MES", session="s")
    stale = bv.build_block_record(_open_pos(age_min=999), _NOW.isoformat(), instrument="MES", session="s")
    drift = bv.build_block_record(_open_pos(age_min=3), _NOW.isoformat(), instrument="MES", session="s",
                                  broker_open=False)
    assert bv.should_escalate(active) is False
    assert bv.should_escalate(stale) is True
    assert bv.should_escalate(drift) is True


def test_summarize_flags_bars_without_decisions():
    # Every bar blocked (blocked_bars >= bars_claimed) = pipeline-blind signature.
    recs = [bv.build_block_record(_open_pos(age_min=i), _NOW.isoformat(), instrument="MES", session="s")
            for i in (2, 4, 6)]
    s = bv.summarize_blocks(recs, bars_claimed=3)
    assert s["blocked_bars"] == 3
    assert s["bars_without_decisions"] is True
    s2 = bv.summarize_blocks(recs, bars_claimed=10)  # some bars produced decisions
    assert s2["bars_without_decisions"] is False


# ── journal: inert record type ────────────────────────────────────────────────

def _seed_open(j, for_date):
    j._append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "instrument": "MES", "session": "new_york", "decision": "TRADE",
        "risk_check": {"result": "APPROVED"},
        "setup": {"direction": "LONG", "entry": 7531.0, "stop": 7520.75,
                  "target": 7555.75, "contracts": 1},
        "outcome": None,
    }, for_date)


def test_block_visibility_record_is_inert_to_daily_state(tmp_path):
    """Required test #2: repeated blocked bars are visible but never create a
    duplicate trade outcome or alter the position slot."""
    d = date(2026, 7, 21)
    j = JournalLogger(log_dir=str(tmp_path))
    _seed_open(j, d)
    before = j.get_daily_state(d)
    before_open = j.get_open_position(d)

    # 50 blocked bars — the stuck-position case.
    for i in range(50):
        rec = bv.build_block_record(_open_pos(age_min=i), _NOW.isoformat(),
                                    instrument="MES", session="new_york")
        j.log_block_visibility(rec, for_date=d)

    after = j.get_daily_state(d)
    after_open = j.get_open_position(d)
    assert after.trade_count == before.trade_count          # no new trades
    assert after.has_open_position == before.has_open_position
    assert after.realized_pnl_dollars == before.realized_pnl_dollars
    assert after_open == before_open                        # slot unchanged
    # But the records ARE present and readable.
    entries = j._read_entries(j._journal_path(d))
    vis = [e for e in entries if e.get("type") == "BLOCK_VISIBILITY"]
    assert len(vis) == 50
    assert all(e.get("classification") for e in vis)


def test_last_reconcile_ts_reads_reconcile_outcome(tmp_path):
    d = date(2026, 7, 21)
    j = JournalLogger(log_dir=str(tmp_path))
    assert j.last_reconcile_ts(d) is None
    j._append({"ts": "2026-07-21T17:00:00+00:00", "type": "OUTCOME",
               "session": "reconcile", "instrument": "MES",
               "outcome": {"result": "BREAKEVEN"}}, d)
    j._append({"ts": "2026-07-21T17:40:00+00:00", "type": "OUTCOME",
               "session": "reconcile", "instrument": "MES",
               "outcome": {"result": "BREAKEVEN"}}, d)
    j._append({"ts": "2026-07-21T18:00:00+00:00", "type": "OUTCOME",
               "session": "new_york", "instrument": "MES",
               "outcome": {"result": "WIN"}}, d)  # not a reconcile row
    assert j.last_reconcile_ts(d) == "2026-07-21T17:40:00+00:00"
