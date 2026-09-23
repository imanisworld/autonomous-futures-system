"""Options first-sight vs trigger-reclaim evaluator (prereg #949)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from research import options_reclaim_entry as oe

UTC = timezone.utc
REPO = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 9, 24, 14, 0, tzinfo=UTC)  # after the forward start


def _db(tmp_path: Path) -> Path:
    p = tmp_path / "opt.sqlite"
    c = sqlite3.connect(p)
    c.executescript(
        """
        create table scans (id integer primary key, timestamp text);
        create table options_shadow_journal (id integer primary key, timestamp text, scan_id int, ticker text,
          direction text, score real, pattern text, status text, setup_inputs_json text, provider_snapshot_json text,
          selected_contract_json text, outcome_json text);
        create table options_contract_marks (id integer primary key, shadow_id int, timestamp text, option_symbol text,
          bid real, ask real, mid real, volume real, open_interest real, delta real, gamma real, theta real,
          implied_volatility real, quote_timestamp text, error text, raw_json text);
        create table options_v1_diagnostic_snapshots (id integer primary key, shadow_id int, timestamp text, event text,
          underlying_price real, setup_entry_trigger real, option_bid real, option_ask real, option_mid real,
          quote_timestamp text, delta real, gamma real, theta real, implied_volatility real, error text,
          setup_type text, setup_timeframe text, paper_evidence_lane text, raw_json text);
        insert into scans(timestamp) values ('2026-09-24T20:00:00+00:00');
        """
    )
    c.commit()
    c.close()
    return p


def _add(db: Path, sid: int, *, direction="LONG", price=99.0, trigger=100.0, stop=95.0, target=110.0,
         ask=5.0, bid=4.9, path=(), status="OPEN", outcome=None, lane=None, opened=T0, symbol="XYZ261120C00100000",
         quote_ts=True, marks_symbol=None, geometry="AHEAD"):
    c = sqlite3.connect(db)
    sc = {"paper_policy_id": "OPTIONS_PAPER_V1", "contract": symbol, "expiry": "2026-11-20", "stop": stop,
          "target": target, "option_ask": ask, "option_bid": bid, "entry_quote": ask, "premium_stop": round(ask * 0.75, 4),
          "paper_entry_geometry": geometry}
    if lane:
        sc["paper_evidence_lane"] = lane
    si = {"price": price, "setup_entry_trigger": trigger, "underlying_invalidation": stop, "target": target}
    c.execute("insert into options_shadow_journal values (?,?,?,?,?,?,?,?,?,?,?,?)",
              (sid, opened.isoformat(), 1, "XYZ", direction, 1.0, "p", status, json.dumps(si), "{}", json.dumps(sc),
               json.dumps(outcome or {})))
    rows = [(opened, "ENTRY", price, bid, ask)] + [
        (opened + timedelta(minutes=5 * (i + 1)), "MARK", px, b, a) for i, (px, b, a) in enumerate(path)]
    for ts, ev, px, b, a in rows:
        qts = ts.isoformat() if quote_ts else None
        c.execute("insert into options_v1_diagnostic_snapshots(shadow_id,timestamp,event,underlying_price,setup_entry_trigger,"
                  "option_bid,option_ask,quote_timestamp,error) values (?,?,?,?,?,?,?,?,'')",
                  (sid, ts.isoformat(), ev, px, trigger, b, a, qts))
        c.execute("insert into options_contract_marks(shadow_id,timestamp,option_symbol,bid,ask,volume,open_interest,delta,"
                  "quote_timestamp,error) values (?,?,?,?,?,?,?,?,?,'')",
                  (sid, ts.isoformat(), marks_symbol or symbol, b, a, 500, 5000, 0.45, qts))
    c.commit()
    c.close()


def _load(db):
    conn = oe.connect_readonly(db)
    return conn, oe.load_episodes(conn)


def test_frozen_constants_match_prereg_and_v1():
    doc = (REPO / oe.PREREG_DOC).read_text()
    assert "50 eligible paired episodes" in doc and "20 distinct trading days" in doc and "30 actual RECLAIM entries" in doc
    assert (oe.MIN_ELIGIBLE_PAIRS, oe.MIN_DISTINCT_DAYS, oe.MIN_RECLAIM_ENTRIES) == (50, 20, 30)
    from alert_ranker import paper_v1 as pv

    assert oe.PREMIUM_STOP_MULTIPLIER == pv.PREMIUM_STOP_MULTIPLIER
    assert oe.MAX_TRADE_RISK_DOLLARS == pv.MAX_TRADE_RISK_DOLLARS
    assert oe.MAX_AGGREGATE_OPEN_RISK_DOLLARS == pv.MAX_AGGREGATE_OPEN_RISK_DOLLARS
    assert oe.MAX_SPREAD_PERCENT == pv.MAX_SPREAD_PERCENT
    assert oe.FORWARD_START == datetime(2026, 9, 23, 14, 7, 8, tzinfo=UTC)


def test_control_matches_production_resolution_order(tmp_path):
    db = _db(tmp_path)
    # premium stop (bid <= 3.75) before the underlying stop
    _add(db, 1, path=[(98.0, 4.5, 4.6), (97.0, 3.7, 3.8)])
    # target hit -> exit at bid
    _add(db, 2, path=[(105.0, 6.0, 6.1), (110.5, 8.0, 8.1)])
    # premium stop and target on the same snapshot -> pessimistic ambiguous LOSS
    _add(db, 3, path=[(111.0, 3.0, 3.2)])
    # missing bid keeps the row open
    _add(db, 4, path=[(94.0, None, 4.0)])
    _, eps = _load(db)
    c = {e.shadow_id: oe.control_arm(e) for e in eps}
    assert c[1]["closed_reason"] == "premium_stop_hit" and c[1]["pnl"] == pytest.approx(-130.0)
    assert c[2]["closed_reason"] == "target_hit" and c[2]["exit"] == 8.0 and c[2]["pnl"] == pytest.approx(300.0)
    assert c[3]["closed_reason"] == "ambiguous_same_snapshot_pessimistic_loss" and c[3]["status"] == "LOSS"
    assert c[4]["state"] == "OPEN"


def test_reproduction_passes_on_stored_rows_and_fails_on_lineage_break(tmp_path):
    db = _db(tmp_path)
    t_exit = T0 + timedelta(minutes=10)
    stored = {"closed_reason": "target_hit", "exit_mark": 8.0, "pnl_dollars": 300.0,
              "resolved_at": t_exit.astimezone(oe.ET).replace(tzinfo=None).isoformat()}
    _add(db, 1, path=[(105.0, 6.0, 6.1), (110.5, 8.0, 8.1)], status="WIN", outcome=stored)
    conn, eps = _load(db)
    rep = oe.reproduction(eps, oe.contract_symbols_by_row(conn))
    assert rep["verdict"] == "PASS" and rep["matched"] == 1
    (tmp_path / "b").mkdir()
    db2 = _db(tmp_path / "b")
    _add(db2, 1, path=[(105.0, 6.0, 6.1), (110.5, 8.0, 8.1)], status="WIN", outcome=stored,
         marks_symbol="XYZ261120C00105000")  # a different contract was quoted
    conn2, eps2 = _load(db2)
    rep2 = oe.reproduction(eps2, oe.contract_symbols_by_row(conn2))
    assert rep2["verdict"] == "FAIL" and rep2["rows"][0]["contract_symbol_lineage"] is False


def test_eligibility_rules(tmp_path):
    db = _db(tmp_path)
    _add(db, 1)                                                      # eligible
    _add(db, 2, opened=datetime(2026, 9, 23, 14, 0, tzinfo=UTC))     # before forward start
    _add(db, 3, price=101.0)                                         # first seen above trigger (LONG)
    _add(db, 4, quote_ts=False)                                      # no decision-time quote timestamp
    _add(db, 5, lane="COUNTERFACTUAL")                               # not ACTIVE -> not loaded
    _add(db, 6, direction="SHORT", price=101.0, trigger=100.0, stop=105.0, target=90.0)  # SHORT failed side
    _add(db, 7, geometry="TARGET_CONSUMED_AT_ENTRY")
    _, eps = _load(db)
    got = {e.shadow_id: oe.eligibility(e)[1] for e in eps}
    assert got == {1: "eligible", 2: "before_forward_start", 3: "first_sight_not_on_failed_side",
                   4: "no_decision_time_quote_evidence", 6: "eligible", 7: "entry_geometry_TARGET_CONSUMED_AT_ENTRY"}


def test_reclaim_enters_on_first_valid_reclaim_with_same_contract(tmp_path):
    db = _db(tmp_path)
    # reclaim at 100.5 (rr = 9.5/5.5 >= 1) -> enter at that ask 5.6; later target -> exit bid 8.0
    _add(db, 1, path=[(99.5, 4.9, 5.0), (100.5, 5.5, 5.6), (110.2, 8.0, 8.1)])
    _, eps = _load(db)
    r = oe.reclaim_arm(eps[0], eps)
    assert r["state"] == "ENTERED" and r["entry"] == 5.6 and r["closed_reason"] == "target_hit"
    assert r["pnl"] == pytest.approx((8.0 - 5.6) * 100)
    assert r["seconds_to_reclaim"] == 600.0


def test_reclaim_no_entry_when_episode_resolves_first(tmp_path):
    db = _db(tmp_path)
    _add(db, 1, path=[(97.0, 4.2, 4.3), (94.5, 3.0, 3.1)])  # stop before any reclaim
    _, eps = _load(db)
    r = oe.reclaim_arm(eps[0], eps)
    assert r["state"] == "NO_ENTRY" and r["pnl"] == 0.0


def test_reclaim_skips_observations_failing_frozen_gates(tmp_path):
    db = _db(tmp_path)
    # first sight rr = 5/4 >= 1; the reclaim at 101.5 leaves rr = 2.5/6.5 < 1 (target 104) -> skipped,
    # never re-thresholded; stop later -> NO_ENTRY
    _add(db, 1, target=104.0, path=[(101.5, 5.5, 5.6), (94.0, 2.0, 2.1)])
    _, eps = _load(db)
    r = oe.reclaim_arm(eps[0], eps)
    assert r["state"] == "NO_ENTRY" and r["ineligible_reclaims_skipped"][0]["failed"] == ["remaining_rr_below_1"]


def test_reclaim_blocked_on_missing_quote_or_data_end(tmp_path):
    db = _db(tmp_path)
    _add(db, 1, path=[(100.5, None, None)])                       # reclaim with no quote -> BLOCKED
    _add(db, 2, path=[(99.0, 4.9, 5.0)])                          # data ends, unresolved -> BLOCKED
    _add(db, 3, path=[(100.5, 5.5, 5.6), (101.0, 5.7, 5.8)])       # entered but marks end open -> BLOCKED
    _, eps = _load(db)
    r = {e.shadow_id: oe.reclaim_arm(e, eps) for e in eps}
    assert r[1] == {"state": "BLOCKED", "reason": "missing_quote_at_reclaim", "at": r[1]["at"]}
    assert r[2]["reason"] == "episode_unresolved_when_contract_marks_end"
    assert r[3]["reason"] == "reclaim_open_when_contract_marks_end"


def test_counts_report_is_blind_and_look_is_gated(tmp_path):
    db = _db(tmp_path)
    _add(db, 1, path=[(99.5, 4.9, 5.0), (100.5, 5.5, 5.6), (110.2, 8.0, 8.1)])
    _add(db, 2, path=[(97.0, 4.2, 4.3), (94.5, 3.0, 3.1)])
    _, eps = _load(db)
    pairs = oe.evaluate(eps)
    rep = oe.counts_report(pairs, as_of=T0 + timedelta(days=1), source={"db": "x"})
    blob = json.dumps(rep, default=str)
    for frag in ("pnl", '"net"', "profit", "drawdown"):
        assert frag not in blob
    assert rep["eligible_scorable_pairs"] == 2 and rep["reclaim_entries_scorable"] == 1 and rep["status"] == "COLLECTING"
    with pytest.raises(oe.LookRefused):
        oe.look_report(pairs, as_of=T0 + timedelta(days=1))


def test_look_verdict_on_a_full_sample(tmp_path):
    db = _db(tmp_path)
    sid = 1
    for day in range(35):
        opened = T0 + timedelta(days=day)
        _add(db, sid, opened=opened, path=[(99.5, 4.9, 5.0), (100.5, 5.5, 5.6), (110.2, 8.0, 8.1)]); sid += 1
        _add(db, sid, opened=opened + timedelta(hours=1), path=[(97.0, 4.2, 4.3), (94.5, 3.0, 3.1)]); sid += 1
    _, eps = _load(db)
    rep = oe.look_report(oe.evaluate(eps), as_of=T0 + timedelta(days=40))
    assert rep["n_pairs"] == 70 and rep["reclaim_entries"] == 35 and rep["days"] >= 20
    assert rep["verdict"] in ("FORWARD TEST SUPPORTS RECLAIM", "NO EVIDENCE OF IMPROVEMENT")
    assert set(rep["criteria"]) == {"reclaim_per_episode_gt_control", "reclaim_total_positive",
                                    "reclaim_pf_gt_control", "reclaim_drawdown_not_worse",
                                    "both_halves_diff_non_negative"}


def test_cli_look_guards(tmp_path, capsys):
    import importlib

    cli = importlib.import_module("scripts.options_reclaim_entry")
    db = _db(tmp_path)
    assert cli.main(["look", "--db", str(db), "--out", str(tmp_path / "l.json")]) == 3
    (tmp_path / "l.json").write_text("{}")
    assert cli.main(["look", "--db", str(db), "--out", str(tmp_path / "l.json"), "--confirm-single-look"]) == 3
    assert "already happened" in capsys.readouterr().err
