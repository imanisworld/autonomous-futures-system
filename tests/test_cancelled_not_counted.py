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
