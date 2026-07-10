"""CANCELLED trades (naked-flatten / phantom) held no position -> must NOT count
toward the daily/per-session trade limit (else a failed attempt locks the session)."""
from datetime import date
from journal.journal_logger import JournalLogger
def _jl(p): return JournalLogger(log_dir=str(p))
def _trade(session="london", ts="2026-06-16T08:00:00+00:00"):
    return {"type":"DECISION","decision":"TRADE","risk_check":{"result":"APPROVED"},"session":session,"ts":ts}
def _outcome(r, ts="2026-06-16T08:05:00+00:00", pnl=0.0):
    return {"type":"OUTCOME","outcome":{"result":r,"pnl_dollars":pnl},"ts":ts}
def test_cancelled_outcome_does_not_count(tmp_path):
    ds=_jl(tmp_path)._compute_daily_state([_trade(),_outcome("CANCELLED")],date(2026,6,16))
    assert ds.session_trade_counts.get("london",0)==0
    assert ds.trade_count==0
    assert ds.has_open_position is False
def test_win_still_counts(tmp_path):
    ds=_jl(tmp_path)._compute_daily_state([_trade(),_outcome("WIN",pnl=55.0)],date(2026,6,16))
    assert ds.session_trade_counts.get("london")==1
    assert ds.trade_count==1
def test_mixed_win_then_cancelled_counts_only_the_win(tmp_path):
    e=[_trade(ts="2026-06-16T08:00:00+00:00"),_outcome("WIN",ts="2026-06-16T08:05:00+00:00",pnl=55.0),_trade(ts="2026-06-16T09:00:00+00:00"),_outcome("CANCELLED",ts="2026-06-16T09:05:00+00:00")]
    ds=_jl(tmp_path)._compute_daily_state(e,date(2026,6,16))
    assert ds.session_trade_counts.get("london")==1
    assert ds.trade_count==1
def test_open_trade_still_counts_and_flags_open(tmp_path):
    ds=_jl(tmp_path)._compute_daily_state([_trade()],date(2026,6,16))
    assert ds.session_trade_counts.get("london")==1
    assert ds.has_open_position is True

# ── Confirmed-execution model (2026-07-10, EXECUTION_STATE_BUG fix) ───────────
# A pre-broker approved intent is logged as decision="TRADE_INTENT" (non-open,
# never counted); only a broker-confirmed decision="TRADE" row is open/counted.
def _intent(session="london", ts="2026-06-16T08:00:00+00:00"):
    return {"type":"DECISION","decision":"TRADE_INTENT","risk_check":{"result":"APPROVED"},"session":session,"ts":ts}

def test_intent_alone_is_not_open_and_not_counted(tmp_path):
    ds=_jl(tmp_path)._compute_daily_state([_intent()],date(2026,6,16))
    assert ds.trade_count==0
    assert ds.session_trade_counts.get("london",0)==0
    assert ds.has_open_position is False

def test_intent_then_confirmed_trade_opens_and_counts(tmp_path):
    e=[_intent(ts="2026-06-16T08:00:00+00:00"),_trade(ts="2026-06-16T08:00:01+00:00")]
    ds=_jl(tmp_path)._compute_daily_state(e,date(2026,6,16))
    assert ds.trade_count==1
    assert ds.session_trade_counts.get("london")==1
    assert ds.has_open_position is True

def test_intent_then_cancelled_leaves_no_phantom_and_no_count(tmp_path):
    # No-fill attempt: intent (uncounted) + standalone CANCELLED. Must NOT open a
    # phantom and must NOT push trade_count negative or clear a nonexistent trade.
    e=[_intent(ts="2026-06-16T08:00:00+00:00"),_outcome("CANCELLED",ts="2026-06-16T08:00:05+00:00")]
    ds=_jl(tmp_path)._compute_daily_state(e,date(2026,6,16))
    assert ds.trade_count==0
    assert ds.session_trade_counts.get("london",0)==0
    assert ds.has_open_position is False

def test_confirmed_win_then_failed_intent_does_not_erase_the_win(tmp_path):
    # THE regression guard: confirmed TRADE -> WIN (count=1), then a NO-FILL attempt
    # that only produced an intent + CANCELLED. The CANCELLED must NOT decrement the
    # already-counted win (which would wrongly re-open the daily trade budget).
    e=[
        _trade(ts="2026-06-16T08:00:00+00:00"),
        _outcome("WIN",ts="2026-06-16T08:05:00+00:00",pnl=55.0),
        _intent(ts="2026-06-16T09:00:00+00:00"),
        _outcome("CANCELLED",ts="2026-06-16T09:00:05+00:00"),
    ]
    ds=_jl(tmp_path)._compute_daily_state(e,date(2026,6,16))
    assert ds.trade_count==1
    assert ds.session_trade_counts.get("london")==1
    assert ds.has_open_position is False

def test_get_open_position_ignores_intent_and_tracks_confirmed(tmp_path):
    jl=_jl(tmp_path); d=date(2026,6,16)
    def _write(rows):
        # exercise the real file-backed reader, not just _compute_daily_state
        for r in rows: jl.log_decision(r, r.get("risk_check"), for_date=d) if r.get("type")=="DECISION" else jl._append(r, d)
    setup={"instrument":"MES","direction":"LONG","entry":5898.5,"stop":5896.0,"target":5904.0,"contracts":1,"strategy":"orb_breakout"}
    intent={"type":"DECISION","decision":"TRADE_INTENT","risk_check":{"result":"APPROVED"},"instrument":"MES","session":"new_york","ts":"2026-06-16T14:00:00+00:00","setup":dict(setup)}
    _write([intent])
    assert jl.get_open_position(d) is None  # intent alone is not an open position
    confirmed={"type":"DECISION","decision":"TRADE","risk_check":{"result":"APPROVED"},"instrument":"MES","session":"new_york","ts":"2026-06-16T14:00:01+00:00","setup":dict(setup)}
    _write([confirmed])
    op=jl.get_open_position(d)
    assert op is not None and op["instrument"]=="MES" and op["direction"]=="LONG"
